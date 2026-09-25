import sqlite3
from datetime import datetime, timezone
import json
import sys

import pytest

from ramon.compare import MomentumBaseline, compare, holdout_metadata, main, observed_cost_r
from ramon.core import Forecast
from ramon.history import ensure_history_db, load_bars


class RisingForecast:
    def forecast(self, closes, horizon):
        start = closes[-1]
        return Forecast(start - 2, start + 2, start + 3,
                        tuple(start + (n + 1) * 0.5 for n in range(horizon)))


def seed(db, missing=False):
    ensure_history_db(db)
    rows = []
    for i in range(620):
        price = 100 + i * 0.15
        rows.append(("XAUUSD_l", "M15", 1_800_000_000 + i * 900,
                     price, price + 0.4, price - 0.4, price + 0.05,
                     0 if missing and i == 600 else 10))
    with sqlite3.connect(db) as conn:
        conn.executemany("""INSERT INTO history_bars
            (symbol,timeframe,time,open,high,low,close,spread_points)
            VALUES (?,?,?,?,?,?,?,?)""", rows)


def test_baseline_uses_only_past_closes():
    model = MomentumBaseline()
    early = [100 + i * 0.1 for i in range(16)]
    prediction = model.forecast(early, 4)
    assert prediction.median > early[-1]
    assert prediction.low <= prediction.median <= prediction.high
    assert model.forecast(early + [300], 4).median != prediction.median


def test_compare_requires_actual_holdout_spreads(tmp_path):
    db = tmp_path / "history.sqlite3"
    seed(db, missing=True)
    with pytest.raises(ValueError, match="without recorded spread"):
        compare(db, RisingForecast())


def test_compare_distinguishes_spread_only_and_explicit_fees(tmp_path):
    db = tmp_path / "history.sqlite3"
    seed(db)
    spread_only = compare(db, RisingForecast(), stride=4)
    with_fees = compare(db, RisingForecast(), stride=4, cost_r=0.1)
    assert spread_only["cost"]["roundtrip_r"] is None
    assert spread_only["holdout"]["bars"] == 124
    assert "start_utc" not in spread_only["holdout"]
    assert spread_only["holdout"]["clock"].startswith("raw MT5 broker-server")
    assert with_fees["cost"]["source"] == "user supplied R per round trip"
    for key in ("chronos", "momentum_baseline"):
        trades = with_fees["results"][key]["buys"] + with_fees["results"][key]["sells"]
        assert trades > 0
        assert with_fees["results"][key]["net_r"] == pytest.approx(
            spread_only["results"][key]["net_r"] - trades * 0.1, abs=0.00011)


def test_observed_fees_normalize_to_account_r(tmp_path):
    db = tmp_path / "history.sqlite3"
    ensure_history_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO trade_outcomes
            (trade_key,sample_key,symbol,direction,opened,closed,net_units,
             initial_risk_units,net_r,exit_reason,received,commission_units,swap_units,fee_units)
            VALUES ('t','s','XAUUSD_l','BUY',1,2,3,10,0.3,'DEAL_REASON_TP',3,-1,0,0)""")
    assert observed_cost_r(db, "XAUUSD_l") == (0.1, 1)


def test_holdout_utc_requires_consistent_nearby_deal_offsets(tmp_path):
    db = tmp_path / "history.sqlite3"
    seed(db)
    bars, _ = load_bars(db, "XAUUSD_l")
    end = bars[-1].time
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO trade_outcomes
            (trade_key,sample_key,symbol,direction,opened,closed,net_units,
             initial_risk_units,net_r,exit_reason,received,opened_utc_offset_seconds)
            VALUES ('t1','s1','XAUUSD_l','BUY',?,?,1,10,0.1,'DEAL_REASON_TP',?,10800)""",
                     (end - 900, end, end))
    holdout = holdout_metadata(db, "XAUUSD_l", bars, 496, 4)
    assert holdout["end_utc"] == datetime.fromtimestamp(end - 10800, timezone.utc).isoformat()
    assert holdout["end_utc_offset_seconds"] == 10800
    assert holdout["end_offset_samples"] == 1
    assert "start_utc" not in holdout

    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO trade_outcomes
            (trade_key,sample_key,symbol,direction,opened,closed,net_units,
             initial_risk_units,net_r,exit_reason,received,closed_utc_offset_seconds)
            VALUES ('t2','s2','XAUUSD_l','SELL',?,?,1,10,0.1,'DEAL_REASON_TP',?,7200)""",
                     (end - 1800, end, end))
    assert "end_utc" not in holdout_metadata(db, "XAUUSD_l", bars, 496, 4)


def test_time_only_does_not_load_chronos(tmp_path, monkeypatch, capsys):
    db = tmp_path / "history.sqlite3"
    seed(db)
    monkeypatch.setattr(sys, "argv", ["compare", "--db", str(db), "--time-only"])
    monkeypatch.setattr("ramon.compare.ChronosForecaster",
                        lambda *args: pytest.fail("time-only must not load Chronos"))
    main()
    report = json.loads(capsys.readouterr().out)
    assert report["holdout"]["bars"] == 124
    assert "end_utc" not in report["holdout"]
