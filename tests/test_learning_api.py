"""Exercise the real HTTP/SQLite boundary using a deterministic forecaster."""
from __future__ import annotations

from dataclasses import asdict
from http.server import HTTPServer
import json
import sqlite3
from threading import Event, Thread
from urllib.request import Request, urlopen

import pytest

from ramon.core import Settings
from ramon.train_roles import load_trade_examples
from test_decision import FixedModel, bars


@pytest.fixture
def learning_server(tmp_path, monkeypatch):
    import ramon.server as service
    db = tmp_path / "history.sqlite3"
    monkeypatch.setenv("RAMON_HISTORY_DB", str(db))
    monkeypatch.setenv("RAMON_ENSEMBLE_DIR", str(tmp_path / "roles"))
    servers = []
    ready = Event()

    def factory(address, handler):
        server = HTTPServer(address, handler)
        servers.append(server)
        ready.set()
        return server

    monkeypatch.setattr(service, "HTTPServer", factory)
    thread = Thread(target=service.serve, args=("127.0.0.1", 0, FixedModel(), Settings()), daemon=True)
    thread.start()
    assert ready.wait(5)
    server = servers[0]
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(route, data):
        request = Request(base + route, json.dumps(data).encode(), {"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    yield db, post
    server.shutdown()
    thread.join(5)
    server.server_close()


def test_decision_to_actual_closed_trade_round_trip(learning_server):
    db, post = learning_server
    candles = bars()
    quote_time = candles[-1].time + 905
    payload = {"symbol": "XAUUSD_l", "timeframe": "M15", "bid": 100.0, "ask": 100.4,
               "point": .01, "bars": [asdict(bar) for bar in candles], "quote_time": quote_time}
    decision = post("/decision", payload)
    assert decision["decision"] == "BUY"
    assert decision["sample_saved"] == 1
    assert len(decision["sample_key"]) == 16
    outcome = {"trade_key": "server:123:987", "sample_key": decision["sample_key"],
               "symbol": "XAUUSD_l", "direction": "BUY", "opened": quote_time + 2,
               "closed": quote_time + 1800, "net_units": -6.2, "initial_risk_units": 6,
               "exit_reason": "DEAL_REASON_SL"}
    assert post("/trades", outcome) == {"saved": True}
    assert post("/trades", outcome) == {"saved": True}
    examples = load_trade_examples(db, "XAUUSD_l", "test/fake")
    assert len(examples) == 1
    assert examples[0].label == 0
    assert examples[0].net_r == pytest.approx(-6.2 / 6)
    with sqlite3.connect(db) as connection:
        sample = connection.execute("SELECT quote_time,stop_distance,target_distance FROM decision_samples").fetchone()
        assert sample == (quote_time, 1.5, 3.0)


def test_recording_failure_does_not_create_unconfirmed_learning_success(learning_server, monkeypatch):
    db, post = learning_server
    import ramon.server as service

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(service, "persist_decision_sample", fail)
    payload = {"symbol": "XAUUSD_l", "timeframe": "M15", "bid": 100.0, "ask": 100.4,
               "point": .01, "bars": [asdict(bar) for bar in bars()]}
    decision = post("/decision", payload)
    assert decision["sample_saved"] == 0
    assert decision["decision"] == "BUY"  # telemetry still cannot invent/override a model decision
