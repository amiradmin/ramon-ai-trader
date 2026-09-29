from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from math import exp, isfinite
import time
from typing import Mapping
from urllib.request import Request, urlopen


DEFAULT_FOREX_FACTORY_JSON = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

LEGACY_NEWS_FEATURES = (
    "source_available",
    "high_impact_near",
    "medium_impact_near",
    "upcoming_high_60m",
    "recent_high_60m",
    "event_density_180m",
    "nearest_high_time_scaled",
    "nearest_event_impact",
    "actual_available",
    "surprise_abs",
)
NEWS_FEATURES = LEGACY_NEWS_FEATURES + (
    "post_high_30m", "release_age_30m", "actual_seen_age_30m",
    "signed_surprise", "surprise_aligned_with_candidate",
    "event_inflation", "event_labor", "event_fed", "event_growth",
)
EventKey = tuple[str, str, int]


@dataclass(frozen=True, slots=True)
class NewsEvent:
    title: str
    country: str
    impact: str
    timestamp: int
    actual: str = ""
    forecast: str = ""
    previous: str = ""


@dataclass(frozen=True, slots=True)
class NewsSnapshot:
    features: dict[str, float]
    source: str
    source_ready: bool
    source_age_seconds: int
    event_title: str
    event_country: str
    event_impact: str
    event_time: int
    event_delta_minutes: float
    error: str = ""
    phase: str = "NONE"
    signed_surprise: float = 0.0
    actual_seen_age_seconds: int = -1

    def payload(self) -> dict[str, object]:
        return {
            "news_source": self.source,
            "news_source_ready": int(self.source_ready),
            "news_source_age_seconds": self.source_age_seconds,
            "news_event_title": self.event_title,
            "news_event_country": self.event_country,
            "news_event_impact": self.event_impact,
            "news_event_time": self.event_time,
            "news_event_delta_minutes": self.event_delta_minutes,
            "news_phase": self.phase,
            "news_signed_surprise": self.signed_surprise,
            "news_actual_seen_age_seconds": self.actual_seen_age_seconds,
        }


def neutral_news_features() -> dict[str, float]:
    return {name: 0.0 for name in NEWS_FEATURES}


def _impact_value(impact: str) -> float:
    return {"High": 1.0, "Medium": 0.6, "Low": 0.25}.get(impact, 0.0)


def _numeric(value: str) -> float | None:
    raw = str(value or "").strip().replace(",", "")
    if not raw:
        return None
    multiplier = 1.0
    if raw[-1:] in {"K", "M", "B", "T"}:
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[raw[-1]]
        raw = raw[:-1]
    raw = raw.rstrip("%")
    try:
        number = float(raw) * multiplier
    except ValueError:
        return None
    return number if isfinite(number) else None


def _surprise_abs(event: NewsEvent) -> tuple[float, float]:
    actual = _numeric(event.actual)
    forecast = _numeric(event.forecast)
    previous = _numeric(event.previous)
    if actual is None or forecast is None:
        return 0.0, 0.0
    scale = max(abs(forecast), abs(previous) if previous is not None else 0.0, 1.0)
    return 1.0, min(abs(actual - forecast) / scale, 5.0) / 5.0


def _signed_surprise(event: NewsEvent) -> float:
    actual, forecast, previous = (_numeric(item) for item in
                                  (event.actual, event.forecast, event.previous))
    if actual is None or forecast is None:
        return 0.0
    scale = max(abs(forecast), abs(previous) if previous is not None else 0.0, 1.0)
    return max(-1.0, min(1.0, (actual - forecast) / (5.0 * scale)))


def _event_category(event: NewsEvent | None) -> str:
    if event is None:
        return "none"
    title = event.title.casefold()
    if any(term in title for term in ("cpi", "pce", "ppi", "inflation")):
        return "inflation"
    if any(term in title for term in ("payroll", "employment", "unemployment", "jobless")):
        return "labor"
    if any(term in title for term in ("fomc", "fed ", "federal funds", "interest rate")):
        return "fed"
    if any(term in title for term in ("gdp", "retail sales", "ism ")):
        return "growth"
    return "other"


def _event_key(event: NewsEvent) -> EventKey:
    return event.country, event.title, event.timestamp


def parse_forex_factory_events(payload: bytes | str) -> tuple[NewsEvent, ...]:
    raw = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
    if not isinstance(raw, list):
        raise ValueError("Forex Factory export must be a JSON array")
    events: list[NewsEvent] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip().replace('"', "'").replace("\\", "/")
        country = str(item.get("country", "")).strip()
        impact = str(item.get("impact", "")).strip().title()
        date_text = str(item.get("date", "")).strip()
        if not title or not country or impact not in {"High", "Medium", "Low"} or not date_text:
            continue
        try:
            when = datetime.fromisoformat(date_text)
        except ValueError:
            continue
        if when.tzinfo is None:
            continue
        events.append(
            NewsEvent(
                title=title[:160],
                country=country[:16],
                impact=impact,
                timestamp=int(when.timestamp()),
                actual=str(item.get("actual", "")).strip()[:64],
                forecast=str(item.get("forecast", "")).strip()[:64],
                previous=str(item.get("previous", "")).strip()[:64],
            )
        )
    return tuple(sorted(events, key=lambda event: event.timestamp))


def build_news_snapshot(
    events: tuple[NewsEvent, ...],
    *,
    now: int,
    source_ready: bool,
    source_age_seconds: int,
    countries: frozenset[str] = frozenset({"USD", "All"}),
    error: str = "",
    actual_first_seen: Mapping[EventKey, int] | None = None,
) -> NewsSnapshot:
    relevant = tuple(event for event in events if event.country in countries)
    if not source_ready:
        relevant = ()

    nearest = min(relevant, key=lambda event: abs(event.timestamp - now), default=None)
    high = tuple(event for event in relevant if event.impact == "High")
    medium = tuple(event for event in relevant if event.impact == "Medium")
    nearest_high = min(high, key=lambda event: abs(event.timestamp - now), default=None)

    def proximity(items: tuple[NewsEvent, ...]) -> float:
        if not items:
            return 0.0
        minutes = min(abs(event.timestamp - now) / 60.0 for event in items)
        return exp(-minutes / 60.0)

    upcoming_high = sum(0 <= event.timestamp - now <= 3600 for event in high)
    recent_high = sum(0 <= now - event.timestamp <= 3600 for event in high)
    density = sum(abs(event.timestamp - now) <= 3 * 3600 for event in relevant)
    high_scaled = 0.0
    if nearest_high is not None:
        high_scaled = max(-1.0, min(1.0, (nearest_high.timestamp - now) / (180.0 * 60.0)))

    actual_available = surprise_abs = 0.0
    if nearest is not None and nearest.timestamp <= now and (
        actual_first_seen is None or _event_key(nearest) in actual_first_seen
    ):
        actual_available, surprise_abs = _surprise_abs(nearest)

    # A future event can be closer than the most recently released one. The
    # post-release signal must be tied to a *past* high-impact release only.
    released = min((event for event in high if 0 <= now - event.timestamp <= 1800
                    and (actual_first_seen is None or _event_key(event) in actual_first_seen)),
                   key=lambda event: now - event.timestamp, default=None)
    observed_age = -1
    if released is not None:
        available, _ = _surprise_abs(released)
        if not available:
            released = None
        elif actual_first_seen is not None:
            observed_age = now - actual_first_seen[_event_key(released)]
            if observed_age < 0:
                released = None
        else:
            observed_age = now - released.timestamp
    phase = ("UNAVAILABLE" if not source_ready else
             "POST_RELEASE" if released is not None else
             "PRE_RELEASE" if nearest_high is not None and 0 < nearest_high.timestamp-now <= 1800
             else "RELEASE_UNCONFIRMED" if nearest_high is not None
                  and 0 <= now-nearest_high.timestamp <= 1800
             else "NORMAL")
    category = _event_category(released or nearest_high)
    signed = _signed_surprise(released) if released is not None else 0.0

    features = {
        "source_available": float(source_ready),
        "high_impact_near": proximity(high),
        "medium_impact_near": proximity(medium),
        "upcoming_high_60m": min(float(upcoming_high), 3.0) / 3.0,
        "recent_high_60m": min(float(recent_high), 3.0) / 3.0,
        "event_density_180m": min(float(density), 8.0) / 8.0,
        "nearest_high_time_scaled": high_scaled,
        "nearest_event_impact": _impact_value(nearest.impact) if nearest else 0.0,
        "actual_available": actual_available,
        "surprise_abs": surprise_abs,
        "post_high_30m": float(released is not None),
        "release_age_30m": (min(1.0, (now-released.timestamp)/1800.0)
                            if released is not None else 0.0),
        "actual_seen_age_30m": (min(1.0, observed_age/1800.0)
                                if released is not None else 0.0),
        "signed_surprise": signed,
        "surprise_aligned_with_candidate": 0.0,  # Set from the candidate direction in the ensemble.
        **{f"event_{name}": float(category == name)
           for name in ("inflation", "labor", "fed", "growth")},
    }
    return NewsSnapshot(
        features=features,
        source="FOREX_FACTORY",
        source_ready=source_ready,
        source_age_seconds=max(0, int(source_age_seconds)),
        event_title=nearest.title if nearest else "NONE",
        event_country=nearest.country if nearest else "NONE",
        event_impact=nearest.impact if nearest else "NONE",
        event_time=nearest.timestamp if nearest else 0,
        event_delta_minutes=((nearest.timestamp - now) / 60.0) if nearest else 0.0,
        error=error,
        phase=phase,
        signed_surprise=signed,
        actual_seen_age_seconds=observed_age,
    )


class ForexFactoryNewsProvider:
    """Cached live macro-event context from Forex Factory's weekly JSON export.

    The provider only exposes information available at request time. It never
    backfills a live decision with later calendar values.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        url: str = DEFAULT_FOREX_FACTORY_JSON,
        refresh_seconds: int = 300,
        timeout_seconds: float = 2.0,
        max_stale_seconds: int = 1800,
        countries: frozenset[str] = frozenset({"USD", "All"}),
    ) -> None:
        self.enabled = bool(enabled)
        self.url = str(url)
        self.refresh_seconds = max(60, int(refresh_seconds))
        self.timeout_seconds = max(0.25, float(timeout_seconds))
        self.max_stale_seconds = max(self.refresh_seconds, int(max_stale_seconds))
        self.countries = countries
        self._events: tuple[NewsEvent, ...] = ()
        self._last_attempt = 0
        self._fetched_at = 0
        self._error = ""
        self._actual_first_seen: dict[EventKey, int] = {}

    def _refresh(self, now: int) -> None:
        self._last_attempt = now
        request = Request(self.url, headers={"User-Agent": "RamonAITrader/0.26"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise ValueError("Forex Factory export too large")
            events = parse_forex_factory_events(body)
            if not events:
                raise ValueError("Forex Factory export contained no usable events")
            self._events = events
            self._fetched_at = now
            self._error = ""
            for event in events:
                if event.timestamp <= now and _surprise_abs(event)[0]:
                    self._actual_first_seen.setdefault(_event_key(event), now)
            self._actual_first_seen = {
                key: seen for key, seen in self._actual_first_seen.items()
                if now-key[2] <= 7*86400
            }
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"[:240]

    def snapshot(self, now: int | None = None) -> NewsSnapshot:
        timestamp = int(time.time() if now is None else now)
        if not self.enabled:
            return build_news_snapshot(
                (),
                now=timestamp,
                source_ready=False,
                source_age_seconds=0,
                countries=self.countries,
                error="disabled",
            )
        near_high = any(event.impact == "High" and event.country in self.countries
                        and abs(event.timestamp-timestamp) <= 1800 for event in self._events)
        interval = min(self.refresh_seconds, 60) if near_high else self.refresh_seconds
        if self._last_attempt == 0 or timestamp - self._last_attempt >= interval:
            self._refresh(timestamp)
        age = timestamp - self._fetched_at if self._fetched_at else self.max_stale_seconds + 1
        ready = bool(self._events) and age <= self.max_stale_seconds
        return build_news_snapshot(
            self._events,
            now=timestamp,
            source_ready=ready,
            source_age_seconds=age,
            countries=self.countries,
            error=self._error,
            actual_first_seen=self._actual_first_seen,
        )

    def status(self, now: int | None = None) -> dict[str, object]:
        snap = self.snapshot(now)
        return {
            "news_source": snap.source,
            "news_source_ready": snap.source_ready,
            "news_source_age_seconds": snap.source_age_seconds,
            "news_last_error": snap.error,
        }
