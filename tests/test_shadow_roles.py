from dataclasses import asdict, replace
import hashlib
from http.server import HTTPServer
import json
from threading import Event, Thread
from urllib.request import Request, urlopen

import pytest

from ramon.bundles import FEATURES, atomic_json
from ramon.core import Settings
from ramon.direction_roles import DirectionCoordinator, train_direction_roles
from ramon.ensemble import RISK_FEATURES
from test_ensemble import _market, _decision
from test_role_learning import constant_model
from test_decision import FixedModel, bars


def shadow_bundle(root, p=0.99, roles=None):
    directory = root / "shadow" / "versions" / "candidate"
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    schemas = {**FEATURES, "buy_quality": RISK_FEATURES, "sell_quality": RISK_FEATURES}
    for role in roles if roles is not None else schemas:
        path = directory / f"{role}.json"
        constant_model(schemas[role], p).save(path)
        hashes[role] = hashlib.sha256(path.read_bytes()).hexdigest()
    atomic_json(directory / "manifest.json", {"mode": "shadow", "schema_version": 1,
                "bundle_id": "candidate", "chronos_model": "test/model",
                "symbol": "XAUUSD_l", "sha256": hashes, "roles": {}})
    atomic_json(root / "shadow" / "current.json", {"bundle_id": "candidate"})
    return directory


@pytest.mark.parametrize("base", ["BUY", "SELL", "WAIT"])
@pytest.mark.parametrize("p", [0.01, 0.99])
def test_coordinator_exposes_operational_probabilities_preserving_base_decision(tmp_path, base, p):
    shadow_bundle(tmp_path, p)
    result, _ = DirectionCoordinator(tmp_path, "test/model").assess(_market(), replace(_decision(), decision=base))
    assert result["decision"] == base
    assert result["reason"] == _decision().reason
    assert result["edge"] == _decision().edge
    assert result["ensemble_active"] == 1
    assert result["risk_model_ready"] == 1
    assert result["risk_multiplier"] == 1
    assert result["risk_probability"] == pytest.approx(p)
    assert result["meta_probability"] == pytest.approx(p)


def test_legacy_quality_bundle_migrates_to_operational_probabilities(tmp_path):
    shadow_bundle(tmp_path, p=0.77)
    decision = replace(_decision(), decision="SELL")
    result, _ = DirectionCoordinator(tmp_path, "test/model").assess(_market(), decision)
    assert result["decision"] == "SELL"
    assert result["ensemble_active"] == 1
    assert result["direction_quality_ready"] == 1
    assert result["buy_success_probability"] == pytest.approx(0.77)
    assert result["sell_success_probability"] == pytest.approx(0.77)
    assert result["full_sl_probability"] == pytest.approx(0.77)


def test_partial_bundle_and_corrupt_role_are_isolated(tmp_path):
    directory = shadow_bundle(tmp_path, roles=["regime", "entry"])
    (directory / "entry.json").write_text("broken")
    coordinator = DirectionCoordinator(tmp_path, "test/model")
    result, _ = coordinator.assess(_market(), _decision())
    assert result["regime_probability"] > .9
    assert result["entry_probability"] == -1
    assert result["meta_probability"] == -1
    assert result["decision"] == "WAIT"
    assert "entry" in result["role_errors"]


def test_bad_pointer_checkpoint_and_other_symbol_cannot_affect_trading(tmp_path):
    shadow_bundle(tmp_path)
    coordinator = DirectionCoordinator(tmp_path, "other/model")
    assert coordinator.error
    decision = replace(_decision(), decision="BUY")
    assert coordinator.assess(_market(), decision)[0]["decision"] == "BUY"
    coordinator = DirectionCoordinator(tmp_path, "test/model")
    result, _ = coordinator.assess(replace(_market(), symbol="OTHER"), decision)
    assert result["regime_probability"] == -1
    assert result["decision"] == "BUY"
    atomic_json(tmp_path / "shadow" / "current.json", {"bundle_id": "../../active"})
    assert DirectionCoordinator(tmp_path, "test/model").assess(_market(), decision)[0]["decision"] == "BUY"


def test_prediction_exception_is_display_error_only(tmp_path):
    shadow_bundle(tmp_path)
    coordinator = DirectionCoordinator(tmp_path, "test/model")
    class Broken:
        def predict_proba(self, features):
            raise RuntimeError("unavailable")
    coordinator.regime = Broken()
    result, _ = coordinator.assess(_market(), replace(_decision(), decision="SELL"))
    assert result["decision"] == "SELL"
    assert result["regime_probability"] == -1
    assert result["meta_probability"] == -1
    assert "regime" in result["role_errors"]


def test_training_regime_without_trades_never_writes_live_pointer(tmp_path, monkeypatch):
    import ramon.direction_roles as module
    from ramon.train_roles import Example
    rows = [Example(i, i+1, {"regime": {name: float(i % 2) for name in FEATURES["regime"]}}, i % 2) for i in range(60)]
    monkeypatch.setattr(module, "load_trade_examples", lambda *args: [])
    monkeypatch.setattr(module, "load_bars", lambda *args: ((), ()))
    monkeypatch.setattr(module, "_regime_dataset", lambda *args: rows)
    active = tmp_path / "active.json"
    active.write_text('{"bundle_id":"unchanged"}')
    live_pointer = tmp_path / "direction_live" / "current.json"
    atomic_json(live_pointer, {"bundle_id": "previous_approved"})
    previous_pointer = live_pointer.read_bytes()
    report = train_direction_roles("unused", tmp_path, "XAUUSD_l", "test/model")
    assert report["roles"]["regime"]["status"] == "live_ready"
    assert report["roles"]["entry"]["status"] == "waiting_for_data"
    assert report["roles"]["meta"]["status"] == "waiting_for_data"
    assert active.read_text() == '{"bundle_id":"unchanged"}'
    coordinator = DirectionCoordinator(tmp_path, "test/model")
    assert report["promotion_gate_passed"] is False
    assert live_pointer.read_bytes() == previous_pointer
    assert coordinator.regime is None
    assert coordinator.entry is None


def test_http_defaults_to_operational_direction_roles(tmp_path, monkeypatch):
    import ramon.server as service
    root = tmp_path / "roles"
    shadow_bundle(root)
    # Match the deterministic service model's identity.
    manifest = root / "shadow" / "versions" / "candidate" / "manifest.json"
    raw = json.loads(manifest.read_text())
    raw["chronos_model"] = FixedModel.model_id
    atomic_json(manifest, raw)
    monkeypatch.setattr(service, "assess_market", lambda *a, **k: {"state": "TREND_UP", "route": "CONFIRMED_MODEL", "allowed_directions": ["BUY"], "version": "test-market-state"})
    monkeypatch.setenv("RAMON_AI_ENGINE_V2_ENABLED", "1")
    monkeypatch.delenv("RAMON_ROLE_MODE", raising=False)
    monkeypatch.setenv("RAMON_ENSEMBLE_DIR", str(root))
    monkeypatch.setenv("RAMON_HISTORY_DB", str(tmp_path / "history.sqlite3"))
    monkeypatch.setenv("RAMON_NEWS_ENABLED", "0")
    ready = Event()
    servers = []
    def factory(address, handler):
        server = HTTPServer(address, handler)
        servers.append(server)
        ready.set()
        return server
    monkeypatch.setattr(service, "HTTPServer", factory)
    thread = Thread(target=service.serve, args=("127.0.0.1", 0, FixedModel(), Settings(require_direction_confirmation=False, market_state_policy_enabled=False)), daemon=True)
    thread.start()
    assert ready.wait(5)
    server = servers[0]
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        candles = bars()
        data = {"symbol": "XAUUSD_l", "timeframe": "M15", "bid": 100., "ask": 100.4,
                "point": .01, "bars": [asdict(bar) for bar in candles], "quote_time": candles[-1].time+905}
        with urlopen(Request(base+"/decision", json.dumps(data).encode(), {"Content-Type": "application/json"}), timeout=5) as response:
            result = json.load(response)
        assert result["base_decision"] == "BUY"
        assert result["decision"] == "WAIT"
        assert result["reason"] == "ai_engine_v2_direction_ambiguous"
        assert result["ensemble_active"] == 1
        assert result["risk_multiplier"] == 1
        assert result["regime_probability"] > .9
        assert result["direction_quality_ready"] == 1
        assert result["buy_success_probability"] > .9
        assert result["sell_success_probability"] > .9
        assert result["timesfm3_experimental_effect"] == "NONE"
        assert result["sample_saved"] == 1
        with urlopen(base+"/health", timeout=5) as response:
            assert json.load(response)["ensemble_mode"] == "direction_live"
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
