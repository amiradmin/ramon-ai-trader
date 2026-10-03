"""Truthfulness, freshness and read-only boundaries of the live flow monitor."""
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from ramon.monitor import build_snapshot, freshness, handler_for, read_diagnostic, read_history


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
        assert error.value.code == 501
        assert called == ["http://127.0.0.1:8012/health"]
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()
