"""Truthfulness, freshness and read-only boundaries of the live flow monitor."""
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from ramon.monitor import build_snapshot, dollar_readiness, freshness, handler_for, income_roadmap, read_diagnostic, read_history, read_open_dashboard_positions


NOW = 1_800_000_000


def diagnostic(path, *, sample="sample-1", now=NOW, status="insufficient_model_strength", position="NONE", role="PRIMARY", encoding="utf-8"):
    captured = datetime.fromtimestamp(now, timezone.utc).strftime("%Y.%m.%d %H:%M:%S UTC")
    path.write_text(f"""=== RAMON DIAGNOSTIC ===
EA version: 0.54.8
EA role: {role}  Magic: 26092212
Symbol: XAUUSD_l  Timeframe: M15
Captured: {captured}
DecisionID: {sample}  Saved: YES
Range MAIN: YES | midpoint TP
Range MAIN: ACTIVE  TargetUnits: 5
Live: ARMED  AccountLock: OK
Trade permissions: terminal=YES ea=YES account=YES
Status: {status}
Managed position: {position}
RiskGate: WOULD ALLOW MIN LOT: override <= $0.35
""", encoding=encoding)


@pytest.fixture
def sources(tmp_path):
    db, diag = tmp_path / "history.db", tmp_path / "diagnostic.txt"
    base = {"decision": "WAIT", "reason": "insufficient_model_strength", "forecast_median": 100,
            "buy_edge": 1, "sell_edge": -2, "minimum_edge": .5,
            "signal_strength": .1, "minimum_strength": .2,
            "intrabar_confirmed": 0, "ai_trend_confirmed": 0}
    metadata = {"decision_audit": {"base": base, "final": {"decision": "WAIT", "reason": base["reason"], "role_shadow": 1}, "settings": {}}}
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE decision_samples(id INTEGER PRIMARY KEY, captured INT,symbol TEXT,sample_key TEXT,final_decision TEXT,chronos_model TEXT,model_metadata TEXT)")
        c.execute("INSERT INTO decision_samples VALUES(1,?,?,?,?,?,?)", (NOW, "XAUUSD_l", "sample-1", "WAIT", "chronos", json.dumps(metadata)))
    diagnostic(diag)
    return db, diag


def nodes(snapshot):
    return {n["id"]: n for n in snapshot["nodes"]}


def test_observed_preview_does_not_claim_an_order_or_gate_pass(sources):
    s = build_snapshot(*sources, now=NOW, health={"ready": True})
    n = nodes(s)
    assert s["joined"] and s["read_only"]
    assert n["decision"]["state"] == "blocked"
    assert n["risk"]["state"] == "observed"  # would allow is only a preview
    assert n["limits"]["state"] == "unknown"  # no authoritative cooldown result
    assert n["order"]["state"] == "idle"
    assert n["position"]["state"] == "idle"
    assert n["range"]["state"] == "unknown"  # no persisted rejection detail
    assert n["shadow"]["state"] == "shadow"


@pytest.mark.parametrize("detected,ready,state,signal_ready", [
    ("SELL", 1, "idle", "خیر"),
    ("NEUTRAL", 0, "idle", "خیر"),
    ("BUY", 0, "blocked", "خیر"),
    ("BUY", 1, "pass", "بله"),
])
def test_entry_timing_readiness_belongs_to_the_signal(sources, detected, ready, state, signal_ready):
    db, diag = sources
    with sqlite3.connect(db) as con:
        metadata = json.loads(con.execute("SELECT model_metadata FROM decision_samples").fetchone()[0])
        metadata["decision_audit"]["final"].update({
            "cent_direction_gate_active": 1,
            "market_direction": detected,
            "entry_timing_direction": detected,
            "entry_timing_ready": ready,
        })
        con.execute("UPDATE decision_samples SET model_metadata=?", (json.dumps(metadata),))
    snapshot = build_snapshot(db, diag, now=NOW)
    timing = nodes(snapshot)["entry_timing"]
    assert timing["observed_state"] == state
    assert timing["values"]["آمادهٔ ورود"] == signal_ready
    assert timing["values"]["زمان جهت بررسی تأیید شده"] == ("بله" if ready else "خیر")
    assert timing["values"]["جهت سیگنال"] == "BUY"
    if ready and detected == "SELL":
        assert "هم‌جهت نیست" in timing["detail"]
    assert snapshot["decision"] == "WAIT"


def test_healthy_service_never_refreshes_old_forecast_or_position(sources):
    db, diag = sources
    diagnostic(diag, position="BUY #123 profit=5.00")
    s = build_snapshot(db, diag, now=NOW + 3600, health={"ready": True})
    assert nodes(s)["service"]["state"] == "pass"
    assert nodes(s)["forecast"]["state"] == "stale"
    assert nodes(s)["position"]["state"] == "stale"
    assert nodes(s)["position"]["observed_state"] == "active"
    assert len(s["warnings"]) == 2


def test_file_rewrite_cannot_freshen_recorded_decision(sources):
    db, diag = sources
    diagnostic(diag, now=NOW + 3600)
    s = build_snapshot(db, diag, now=NOW + 3600)
    assert s["ea_freshness"]["state"] == "fresh"
    assert s["model_freshness"]["state"] == "stale"


def test_future_timestamps_are_not_live(sources):
    assert freshness(NOW + 3600, NOW)["state"] == "clock_error"
    assert freshness(float("nan"), NOW)["state"] == "unknown"
    s = build_snapshot(*sources, now=NOW - 3600)
    assert nodes(s)["forecast"]["state"] == "stale"
    assert any("ساعت" in w for w in s["warnings"])


def test_missing_sources_do_not_create_files(tmp_path):
    db, diag = tmp_path / "absent.db", tmp_path / "absent.txt"
    s = build_snapshot(db, diag, now=NOW)
    assert s["decision"] == "UNKNOWN"
    assert not db.exists() and not diag.exists()
    assert all(n["state"] == "unknown" for n in s["nodes"])


def test_reads_do_not_migrate_database(sources):
    db, _ = sources
    before = db.read_bytes()
    build_snapshot(*sources, now=NOW)
    assert db.read_bytes() == before
    assert not Path(str(db) + "-journal").exists()


def test_exact_id_join_does_not_merge_newer_unrelated_decision(sources):
    db, diag = sources
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO decision_samples SELECT 2, captured+1, symbol,'another', 'SELL', chronos_model, model_metadata FROM decision_samples WHERE id=1")
    sample, recent, _, _ = read_history(db, "XAUUSD_l", "sample-1")
    assert sample["sample_key"] == "sample-1"
    assert recent[0]["sample_key"] == "another"
    diagnostic(diag, sample="not-recorded")
    s = build_snapshot(db, diag, now=NOW)
    assert not s["joined"] and any("شناسه" in w for w in s["warnings"])


def test_primary_role_only_utf16_and_duplicate_keys(sources):
    _, diag = sources
    diagnostic(diag, encoding="utf-16")
    parsed, error = read_diagnostic(diag)
    assert error is None and parsed["Range MAIN"].startswith("YES")
    diagnostic(diag, role="SMALL 2c")
    parsed, error = read_diagnostic(diag)
    assert parsed == {} and error is not None


def test_wrong_symbol_is_not_used_for_current_terminal_state(sources):
    db, diag = sources
    diag.write_text(diag.read_text().replace("Symbol: XAUUSD_l", "Symbol: EURUSD"))
    s = build_snapshot(db, diag, now=NOW)
    assert s["ea_freshness"]["state"] == "unknown"
    assert nodes(s)["account"]["state"] == "unknown"


def test_http_surface_only_reads_health_and_cannot_request_a_trade(sources, monkeypatch):
    import ramon.monitor as monitor
    called = []

    def unavailable(url, **kwargs):
        called.append(url)
        raise OSError("offline")

    monkeypatch.setattr(monitor, "urlopen", unavailable)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(*sources, "XAUUSD_l", "http://127.0.0.1:8012/health"))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        for route in ("/", "/app.js", "/style.css", "/api/snapshot"):
            with urlopen(url + route, timeout=3) as response:
                body = response.read()
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
                if route == "/api/snapshot":
                    assert json.loads(body)["read_only"] is True
        for route in ("/decision", "/trades", "/../core.py"):
            with pytest.raises(HTTPError) as error:
                urlopen(url + route, timeout=3)
            assert error.value.code == 404
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url + "/api/snapshot", data=b"{}"), timeout=3)
        assert error.value.code == 404
        assert called == ["http://127.0.0.1:8012/health"]
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()


def test_dollar_readiness_requires_enough_forward_evidence():
    rows = []
    base = NOW - 22 * 86400
    for i in range(120):
        net_r = 0.18 if i % 2 == 0 else -0.10
        rows.append({
            "opened": base + i * 14400,
            "closed": base + i * 14400 + 1800,
            "net_units": net_r * 10,
            "net_r": net_r,
            "entry_ea_version": "0.60.0" if i >= 80 else "0.59.0",
        })
    result = dollar_readiness(rows)
    assert result["trade_count"] == 120
    assert result["profit_factor"] > 1.25
    assert result["current_version_trades"] == 40
    assert result["status"] == "READY"
    assert result["ready"] is True


def test_readiness_uses_running_version_even_before_its_first_close():
    rows = [{"closed": NOW, "entry_ea_version": "0.54.8", "net_units": 1}]
    result = dollar_readiness(rows, "0.56.3")
    assert result["current_ea_version"] == "0.56.3"
    assert result["current_version_trades"] == 0
    assert dollar_readiness([], "0.56.3")["current_ea_version"] == "0.56.3"


def test_dollar_readiness_does_not_promote_small_sample():
    rows = [{
        "opened": NOW - 3600,
        "closed": NOW - 1800,
        "net_units": 1.0,
        "net_r": 0.5,
        "entry_ea_version": "0.60.0",
    } for _ in range(10)]
    result = dollar_readiness(rows)
    assert result["trade_count"] == 10
    assert result["status"] == "NOT READY"
    assert result["ready"] is False


def test_income_roadmap_starts_with_validation_on_cent_account():
    readiness = {
        "ready": False, "profit_factor": 1.1, "max_drawdown_r": 5.0,
        "span_days": 10.0, "current_version_trades": 20,
    }
    road = income_roadmap(readiness, {
        "AccountType": "CENT (configured)",
        "BalanceUnits": "3000.00  BalanceUSDApprox: 30.00  EquityUSDApprox: 30.00",
    })
    assert road["current_stage"] == "validate"
    assert road["stages"][0]["state"] == "current"
    assert all(s["state"] == "locked" for s in road["stages"][1:])


def test_income_roadmap_moves_to_dollar_pilot_only_after_ready():
    readiness = {
        "ready": True, "profit_factor": 1.31, "max_drawdown_r": 4.0,
        "span_days": 25.0, "current_version_trades": 55,
    }
    road = income_roadmap(readiness, {
        "AccountType": "CENT (configured)",
        "BalanceUnits": "3500.00  BalanceUSDApprox: 35.00  EquityUSDApprox: 35.00",
    })
    assert road["stages"][0]["state"] == "done"
    assert road["current_stage"] == "pilot30"
    assert road["stages"][1]["state"] == "current"


def test_income_roadmap_recognizes_standard_scale_stage():
    readiness = {
        "ready": True, "profit_factor": 1.35, "max_drawdown_r": 5.0,
        "span_days": 35.0, "current_version_trades": 60,
    }
    road = income_roadmap(readiness, {
        "AccountType": "STANDARD (configured)",
        "BalanceUnits": "150.00  BalanceUSDApprox: 150.00  EquityUSDApprox: 150.00",
    })
    assert road["stages"][0]["done"] is True
    assert road["stages"][1]["done"] is True
    assert road["stages"][2]["done"] is True
    assert road["current_stage"] == "scale500"


def test_income_roadmap_includes_estimated_time_for_every_stage():
    readiness = {
        "ready": False, "profit_factor": 1.1, "max_drawdown_r": 5.0,
        "span_days": 10.0, "current_version_trades": 20,
    }
    road = income_roadmap(readiness, {
        "AccountType": "CENT (configured)",
        "BalanceUnits": "3000.00  BalanceUSDApprox: 30.00  EquityUSDApprox: 30.00",
    })
    assert len(road["stages"]) == 6
    assert all(stage.get("eta") for stage in road["stages"])
    assert road["stages"][0]["eta"] == "حدود ۲–۳ هفته"
    assert road["stages"][-1]["eta"] == "حدود ۹–۱۸ ماه"


def trace_trade(db, *, sample="sample-1", role="MAIN"):
    with sqlite3.connect(db) as c:
        c.execute("""CREATE TABLE trade_outcomes(
            trade_key TEXT, sample_key TEXT, symbol TEXT, direction TEXT,
            opened INT, closed INT, net_units REAL, trade_role TEXT,
            opened_utc_offset_seconds INT, closed_utc_offset_seconds INT)""")
        c.execute("INSERT INTO trade_outcomes VALUES(?,?,?,?,?,?,?,?,?,?)",
                  ("server:account:position", sample, "XAUUSD_l", "BUY", NOW-60, NOW,
                   -8.82, role, 10800, None))


def test_trade_trace_exact_join_does_not_use_latest_decision(sources):
    from ramon.monitor import trade_trace
    db, _ = sources
    trace_trade(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO decision_samples VALUES(2,?,?,?,?,?,?)",
                  (NOW+60, "XAUUSD_l", "unrelated", "SELL", "new-model", '{}'))
    result = trade_trace(db, "XAUUSD_l", "server:account:position")
    assert result["sample_key"] == "sample-1"
    assert result["decision_joined"] is True
    assert result["direction_matches"] is False  # stored WAIT cannot be called BUY
    route = next(s for s in result["stages"] if s["id"] == "route")
    assert route["values"]["تصمیم نهایی"] == "WAIT"
    assert result["closed"]["at"] is None
    assert result["closed"]["broker_at"] is not None
    assert result["opened"]["at"] == datetime.fromtimestamp(NOW-60-10800, timezone.utc).isoformat()
    assert result["warnings"]
    assert trade_trace(db, "OTHER", "server:account:position") is None
    assert trade_trace(db, "XAUUSD_l", "' OR 1=1 --") is None


def test_trade_trace_without_saved_decision_keeps_model_unknown(sources):
    from ramon.monitor import trade_trace
    db, _ = sources
    trace_trade(db, sample="not-saved")
    result = trade_trace(db, "XAUUSD_l", "server:account:position")
    assert result["decision_joined"] is False
    assert result["direction_matches"] is None
    assert all(s["state"] == "unknown" for s in result["stages"][:7])
    assert result["warnings"]
    with sqlite3.connect(db) as c:
        c.execute("UPDATE trade_outcomes SET trade_role='SMALL'")
    assert trade_trace(db, "XAUUSD_l", "server:account:position") is None


def test_trade_trace_http_is_read_only_and_does_not_call_live_service(sources, monkeypatch):
    from urllib.parse import quote
    import ramon.monitor as monitor
    db, _ = sources
    trace_trade(db)
    monkeypatch.setattr(monitor, "urlopen", lambda *args, **kwargs: pytest.fail("historical trace must not contact service"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(*sources, "XAUUSD_l", "http://unused/health"))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/trade-trace"
    before = db.read_bytes()
    try:
        with urlopen(url + "?trade_key=" + quote("server:account:position")) as response:
            assert json.loads(response.read())["sample_key"] == "sample-1"
        for suffix, code in (("", 400), ("?trade_key=missing", 404)):
            with pytest.raises(HTTPError) as error:
                urlopen(url + suffix)
            assert error.value.code == code
        assert db.read_bytes() == before
    finally:
        server.shutdown(); thread.join(3); server.server_close()


@pytest.mark.parametrize("veto, expected", [("NO", "pass"), ("YES", "blocked")])
def test_model_map_green_requires_ready_gate_without_veto(sources, veto, expected):
    db, diag = sources
    with sqlite3.connect(db) as c:
        raw = c.execute("SELECT model_metadata FROM decision_samples WHERE id=1").fetchone()[0]
        metadata = json.loads(raw)
        metadata["decision_audit"]["external_models"] = {
            "moment": {"moment_shadow_ready": 1}, "finbert": {"finbert_shadow_ready": 1}}
        metadata["decision_audit"]["news_snapshot"] = {"news_source_ready": 1}
        c.execute("UPDATE decision_samples SET model_metadata=? WHERE id=1", (json.dumps(metadata),))
    with diag.open("a") as f:
        f.write(f"MOMENT LIVE: YES Fresh: YES Veto: {veto}\nFinBERT LIVE: YES Veto: {veto}\n")
    result = build_snapshot(db, diag, now=NOW)
    rows = {r["name"]: r for r in result["model_handler_map"]}
    assert rows["Anomaly Detection"]["condition_state"] == expected
    assert rows["News Sentiment"]["condition_state"] == expected
    assert rows["News Calendar"]["condition_state"] == "pass"
    assert rows["Entry"]["condition_state"] == "shadow"
    assert rows["Risk / SL"]["condition_state"] == "shadow"
    assert all(r["condition_state"] == "stale" for r in build_snapshot(db, diag, now=NOW+100)["model_handler_map"])
    diagnostic(diag, sample="different-decision")
    with diag.open("a") as f:
        f.write("MOMENT LIVE: YES Fresh: YES Veto: NO\nFinBERT LIVE: YES Veto: NO\n")
    rows = {r["name"]: r for r in build_snapshot(db, diag, now=NOW)["model_handler_map"]}
    assert rows["Anomaly Detection"]["condition_state"] == "unknown"
    assert rows["News Sentiment"]["condition_state"] == "unknown"


@pytest.mark.parametrize('reason,conflict,extension', [
    ('trend_conflict', 'blocked', 'unknown'),
    ('adverse_intrabar_timing', 'pass', 'unknown'),
    ('late_entry_extension', 'pass', 'blocked'),
    ('insufficient_model_strength', 'pass', 'pass'),
])
def test_conflict_and_extension_are_independent(sources, reason, conflict, extension):
    db, diag = sources
    with sqlite3.connect(db) as c:
        metadata = json.loads(c.execute('SELECT model_metadata FROM decision_samples').fetchone()[0])
        metadata['decision_audit']['base']['reason'] = reason
        c.execute('UPDATE decision_samples SET model_metadata=?', (json.dumps(metadata),))
    snapshot = build_snapshot(db, diag, now=NOW)
    result = nodes(snapshot)
    assert result['timing']['observed_state'] == conflict
    assert result['extension']['observed_state'] == extension


def test_trend_conflict_detail_explains_failed_override(sources):
    db, diag = sources
    with sqlite3.connect(db) as con:
        metadata = json.loads(con.execute("SELECT model_metadata FROM decision_samples").fetchone()[0])
        base = metadata["decision_audit"]["base"]
        base.update({
            "reason": "trend_conflict",
            "signal_strength": 0.61,
            "minimum_strength": 0.20,
            "trend_conflict_active": 1,
            "trend_conflict_override_strength": 0.70,
            "trend_conflict_override_passed": 0,
            "recent_move_atr": -3.42,
            "aligned_recent_move_atr": -3.42,
            "intrabar_confirmed": 1,
            "intrabar_direction": "BUY",
            "ai_trend_confirmed": 1,
            "ai_trend_direction": "BUY",
        })
        metadata["decision_audit"]["settings"].update({
            "trend_conflict_lookback": 12,
            "trend_conflict_atr": 3.0,
            "trend_conflict_override_strength": 0.70,
        })
        metadata["decision_audit"]["final"].update({"decision": "WAIT", "reason": "trend_conflict"})
        con.execute("UPDATE decision_samples SET model_metadata=?", (json.dumps(metadata),))

    timing = nodes(build_snapshot(db, diag, now=NOW))["timing"]
    assert timing["observed_state"] == "blocked"
    assert timing["values"]["حرکت اخیر / ATR"] == -3.42
    assert timing["values"]["حد قدرت عبور"] == 0.70
    assert timing["values"]["قدرت کافی برای عبور"] == "خیر"
    assert timing["values"]["تأیید کوتاه‌مدت"] == "بله"
    assert timing["values"]["تأیید مسیر Chronos"] == "بله"
    assert timing["values"]["نتیجهٔ override"] == "مسدود"
    assert "قدرت Chronos" in timing["detail"]


def test_trend_conflict_detail_marks_confirmed_override_as_pass(sources):
    db, diag = sources
    with sqlite3.connect(db) as con:
        metadata = json.loads(con.execute("SELECT model_metadata FROM decision_samples").fetchone()[0])
        base = metadata["decision_audit"]["base"]
        base.update({
            "decision": "BUY",
            "reason": "forecast_up",
            "signal_strength": 0.81,
            "minimum_strength": 0.20,
            "trend_conflict_active": 1,
            "trend_conflict_override_strength": 0.70,
            "trend_conflict_override_passed": 1,
            "recent_move_atr": -3.30,
            "aligned_recent_move_atr": -3.30,
            "intrabar_confirmed": 1,
            "intrabar_direction": "BUY",
            "ai_trend_confirmed": 1,
            "ai_trend_direction": "BUY",
        })
        metadata["decision_audit"]["settings"].update({
            "trend_conflict_lookback": 12,
            "trend_conflict_atr": 3.0,
            "trend_conflict_override_strength": 0.70,
        })
        metadata["decision_audit"]["final"].update({"decision": "BUY", "reason": "forecast_up"})
        con.execute("UPDATE decision_samples SET final_decision='BUY', model_metadata=?", (json.dumps(metadata),))

    timing = nodes(build_snapshot(db, diag, now=NOW))["timing"]
    assert timing["observed_state"] == "pass"
    assert timing["values"]["قدرت کافی برای عبور"] == "بله"
    assert timing["values"]["نتیجهٔ override"] == "عبور مجاز"
    assert "مجاز شد" in timing["detail"]


def test_open_position_snapshot_requires_fresh_mt5_confirmation(tmp_path, monkeypatch):
    from ramon import monitor
    stamp = 1_800_000_000
    monkeypatch.setattr(monitor.time, "time", lambda: stamp)
    diag = tmp_path / "Ramon_Diagnostic.txt"
    opened = tmp_path / "Ramon_OpenDashboardPositions.txt"
    sample = "abcdef1234567890"
    opened.write_text(f"{sample}|BUY|50379117083|{stamp - 30}|1.73|0.01\n")
    os.utime(opened, (stamp, stamp))

    diagnostic(diag, now=stamp, position="NONE")
    assert read_open_dashboard_positions(diag) == {}

    diagnostic(diag, now=stamp, position="BUY #50379117083")
    assert read_open_dashboard_positions(diag)[sample]["ticket"] == "50379117083"

    os.utime(opened, (stamp - 180, stamp - 180))
    assert read_open_dashboard_positions(diag) == {}

    os.utime(opened, (stamp, stamp))
    diagnostic(diag, now=stamp - 180, position="BUY #50379117083")
    assert read_open_dashboard_positions(diag) == {}
