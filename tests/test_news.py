from __future__ import annotations

from ramon.news import (
    NEWS_FEATURES,
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
               if name != "nearest_high_time_scaled")
    assert -1.0 <= snap.features["nearest_high_time_scaled"] <= 1.0


def test_neutral_features_are_complete() -> None:
    assert neutral_news_features() == {name: 0.0 for name in NEWS_FEATURES}
