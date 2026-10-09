import json
import sqlite3
import zlib
from pathlib import Path

import pandas as pd
import pytest

from ramon.manual_advisors import forecast_advisor, snapshot_advisors, kronos_advisor
from ramon.kronos_manual_advisor import live_utc_rows, predict_latest, publish


NOW = 1800000900
BAR = NOW // 900 * 900 - 900


def test_saved_chronos_and_timesfm_forecasts_are_distinct_from_trade_decisions():
    audit = {"base":{"forecast_median":102}, "final":{"decision":"SELL"},
             "handlers":{"forecast_model_handler":"chronos-2-small"},
             "settings":{"horizon":4}, "experimental_models":{"timesfm3":{"timesfm3_experimental_ready":1,"timesfm3_experimental_median":98}}}
    result = snapshot_advisors(audit, 100, .4, NOW)
    assert [r["direction"] for r in result] == ["BUY","SELL"]
    assert all(r["display_only"] for r in result)
    assert all("probability" not in r for r in result)
    assert forecast_advisor("test",100.1,100,.2,captured=NOW)["direction"] == "WAIT"
    assert forecast_advisor("test",float("nan"),100,.2,captured=NOW)["status"] == "UNAVAILABLE"


def report(**kwargs):
    return dict({"mode":"DISPLAY_ONLY","model":"NeoQuasar/Kronos-small","symbol":"XAUUSD_l",
                 "generated":NOW,"signal_bar_time":BAR,"horizon_bars":4,"forecast_price":101,
                 "reference_price":100,"neutral_band":.42}, **kwargs)


@pytest.mark.parametrize("override", [{"symbol":"OTHER"},{"generated":NOW+10},{"generated":NOW-901},{"signal_bar_time":BAR-900},{"forecast_price":float("nan")},{"mode":"OFFLINE_SHADOW_ONLY"},{"horizon_bars":0}])
def test_kronos_invalid_stale_future_or_offline_results_never_become_advice(tmp_path, override):
    path = tmp_path / "kronos_live_advice.json"
    path.write_text(json.dumps(report(**override)))
    assert kronos_advisor(tmp_path/"history.db","XAUUSD_l",BAR,now=NOW)["status"] != "READY"


def test_kronos_current_bar_only_and_atomic_publication(tmp_path):
    path = tmp_path / "kronos_live_advice.json"
    publish(path, report())
    item = kronos_advisor(tmp_path/"history.db","XAUUSD_l",BAR,now=NOW)
    assert item["direction"] == "BUY" and item["horizon_bars"] == 4
    assert not path.with_name(path.name+".tmp").exists()
    assert kronos_advisor(tmp_path/"history.db","XAUUSD_l",BAR-900,now=NOW)["status"] == "STALE"


class Predictor:
    def predict(self, **kwargs):
        self.input = kwargs
        return pd.DataFrame({"close":[101]*kwargs["pred_len"]})


def test_worker_excludes_forming_bars_and_uses_real_predictor_output():
    rows = [(BAR-900*i,100,102,99,100) for i in range(4,-1,-1)]
    rows += [(BAR+900,100,102,99,999)]  # Forming candle must not be model input.
    predictor = Predictor()
    result = predict_latest(rows,predictor,symbol="XAUUSD_l",lookback=5,horizon=4,now=NOW)
    assert len(predictor.input["df"]) == 5
    assert list(predictor.input["df"]["close"]) == [100]*5
    assert result["forecast_price"] == 101 and result["signal_bar_time"] == BAR
    assert not any(k in result for k in ("success_probability","trade_authorized"))
    with pytest.raises(ValueError):
        predict_latest(rows[:-1],predictor,symbol="XAUUSD_l",lookback=5,now=NOW+900)


def save_live_context(path, *, offset=10800, quote=None, recorded=NOW):
    bars = [{"time": BAR - 900*i + 10800, "open":100, "high":102,
             "low":99, "close":110+i} for i in range(4,-1,-1)]
    # Use internally valid OHLC and distinguish the newest real candle.
    for bar in bars:
        bar.update(open=bar["close"], high=bar["close"]+1, low=bar["close"]-1)
    context = {"symbol":"XAUUSD_l", "timeframe":"M15", "bars":bars}
    provenance = {"request":{"broker_utc_offset_seconds":offset,
                              "quote_time":NOW+10800 if quote is None else quote}}
    with sqlite3.connect(path) as con:
        con.executescript("""CREATE TABLE inference_audit
            (sample_key TEXT, input_sha256 TEXT, recorded_utc REAL, provenance_json TEXT);
            CREATE TABLE input_blobs (sha256 TEXT, codec TEXT, body BLOB);
            CREATE TABLE decision_samples (sample_key TEXT, symbol TEXT);""")
        con.execute("INSERT INTO inference_audit VALUES ('sample','hash',?,?)",
                    (recorded,json.dumps(provenance)))
        con.execute("INSERT INTO input_blobs VALUES ('hash','zlib-json-v1',?)",
                    (zlib.compress(json.dumps(context).encode()),))
        con.execute("INSERT INTO decision_samples VALUES ('sample','XAUUSD_l')")


def test_broker_clock_uses_latest_exact_input_and_matches_same_table_candle(tmp_path):
    db = tmp_path/"history.db"
    save_live_context(db)
    rows, offset = live_utc_rows(db, "XAUUSD_l", now=NOW)
    assert rows[-1][0] == BAR and rows[-1][4] == 110
    predictor = Predictor()
    payload = predict_latest(rows, predictor, symbol="XAUUSD_l",lookback=5,now=NOW)
    assert list(predictor.input["df"]["close"]) == [114,113,112,111,110]
    payload.update(generated=NOW, clock="UTC_FROM_LIVE_REQUEST",
                   broker_utc_offset_seconds=offset, source_signal_bar_time=BAR+offset)
    publish(tmp_path/"kronos_live_advice.json",payload)
    assert kronos_advisor(db,"XAUUSD_l",BAR+offset,now=NOW)["status"] == "READY"
    assert kronos_advisor(db,"XAUUSD_l",BAR+offset-900,now=NOW)["status"] == "STALE"
    payload["broker_utc_offset_seconds"] = 0
    publish(tmp_path/"kronos_live_advice.json",payload)
    assert kronos_advisor(db,"XAUUSD_l",BAR+offset,now=NOW)["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("override", [{"offset":None},{"offset":10801},
                                      {"recorded":NOW-181},{"quote":NOW}])
def test_unknown_stale_or_inconsistent_clock_never_selects_old_history(tmp_path,override):
    db=tmp_path/"history.db"
    save_live_context(db,**override)
    with pytest.raises(ValueError):
        live_utc_rows(db,"XAUUSD_l",now=NOW)
