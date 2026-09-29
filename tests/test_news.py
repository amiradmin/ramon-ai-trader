from __future__ import annotations

from datetime import datetime, timezone

from ramon.news import (
    NEWS_FEATURES,
    ForexFactoryNewsProvider,
    NewsEvent,
    build_news_snapshot,
    neutral_news_features,
    parse_forex_factory_events,
)


def test_parse_forex_factory_weekly_json() -> None:
    events = parse_forex_factory_events(
        """[
          {"title":"Core PCE Price Index m/m","country":"USD",
           "date":"2026-09-22T08:30:00-04:00","impact":"High",
           "actual":"0.3%","forecast":"0.2%","previous":"0.2%"},
          {"title":"Ignored","country":"USD","date":"not-a-date","impact":"High"}
        ]"""
    )
    assert len(events) == 1
    assert events[0].title == "Core PCE Price Index m/m"
    assert events[0].impact == "High"
    assert events[0].timestamp > 0


def test_news_snapshot_is_causal_and_bounded() -> None:
    now = 1_800_000_000
    events = (
        NewsEvent("Past CPI", "USD", "High", now - 900, "3.0%", "2.0%", "2.5%"),
        NewsEvent("Future FOMC", "USD", "High", now + 1800, "9.9", "1.0", "1.0"),
        NewsEvent("Other currency", "EUR", "High", now + 300, "5", "1", "1"),
    )
    snap = build_news_snapshot(
        events,
        now=now,
        source_ready=True,
        source_age_seconds=20,
    )
    assert set(snap.features) == set(NEWS_FEATURES)
    assert snap.source_ready
    assert snap.event_country == "USD"
    assert snap.features["actual_available"] == 1.0
    # Future actual values must never be used before the release timestamp.
    future_only = build_news_snapshot(
        (events[1],),
        now=now,
        source_ready=True,
        source_age_seconds=20,
    )
    assert future_only.features["actual_available"] == 0.0
    assert future_only.features["surprise_abs"] == 0.0
    assert all(0.0 <= value <= 1.0 for name, value in snap.features.items()
               if name not in {"nearest_high_time_scaled", "signed_surprise",
                               "surprise_aligned_with_candidate"})
    assert -1.0 <= snap.features["nearest_high_time_scaled"] <= 1.0
    unavailable = build_news_snapshot(events, now=now, source_ready=False,
                                      source_age_seconds=1801)
    assert unavailable.phase == "UNAVAILABLE"
    assert unavailable.features["post_high_30m"] == 0.0


def test_neutral_features_are_complete() -> None:
    assert neutral_news_features() == {name: 0.0 for name in NEWS_FEATURES}


def test_released_surprise_is_signed_and_keeps_event_type() -> None:
    now = 1_800_000_000
    positive = NewsEvent("Core CPI m/m", "USD", "High", now - 60,
                         "0.4%", "0.2%", "0.2%")
    future = NewsEvent("FOMC Rate Decision", "USD", "High", now + 30,
                       "9.0%", "1.0%", "1.0%")
    snap = build_news_snapshot((positive, future), now=now, source_ready=True,
                               source_age_seconds=5)
    assert snap.phase == "POST_RELEASE"
    assert snap.signed_surprise > 0
    assert snap.features["post_high_30m"] == 1.0
    assert snap.features["event_inflation"] == 1.0
    assert snap.features["event_fed"] == 0.0
    assert build_news_snapshot((future,), now=now, source_ready=True,
                               source_age_seconds=5).signed_surprise == 0.0
    negative = NewsEvent("Non-Farm Payrolls", "USD", "High", now - 60,
                         "100K", "200K", "150K")
    assert build_news_snapshot((negative,), now=now, source_ready=True,
                               source_age_seconds=5).signed_surprise < 0


def test_actual_preloaded_before_release_waits_for_a_fresh_fetch(monkeypatch) -> None:
    now = 1_800_000_000
    event_time = now + 20
    body = ('[{"title":"Core CPI","country":"USD","impact":"High",'
            f'"date":"{datetime.fromtimestamp(event_time, timezone.utc).isoformat()}",'
            '"actual":"0.4%","forecast":"0.2%"}]').encode()

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit):
            return body

    monkeypatch.setattr("ramon.news.urlopen", lambda request, timeout: Response())
    provider = ForexFactoryNewsProvider(refresh_seconds=300)
    assert provider.snapshot(now).phase == "PRE_RELEASE"
    # The export included a future actual, but it was not observable as a
    # genuine post-release value until the provider fetched it after release.
    after = provider.snapshot(now + 30)
    assert after.phase == "RELEASE_UNCONFIRMED"
    assert after.signed_surprise == 0.0
    confirmed = provider.snapshot(now + 61)
    assert confirmed.phase == "POST_RELEASE"
    assert confirmed.actual_seen_age_seconds == 0
