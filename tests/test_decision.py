from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ramon.core import Bar, Forecast, Market, Settings, evaluate
from ramon.replay import replay
from ramon.server import CachedForecaster, persist_market_safely, serve
from ramon.model import ChronosForecaster


def bars(n: int = 256) -> tuple[Bar, ...]:
    return tuple(Bar(1_800_000_000 + i * 900, 100.0, 100.5, 99.5, 100.0) for i in range(n))


class FixedModel:
    model_id = "test/fake"

    def __init__(self, value: Forecast = Forecast(99.0, 103.0, 105.0)) -> None:
        self.value = value
        self.calls = 0
        self.last_close = None

    def forecast(self, closes: list[float], horizon: int) -> Forecast:
        self.calls += 1
        self.last_close = closes[-1]
        assert horizon == 4
        return self.value


class FakePathTensor:
    def tolist(self) -> list[list[float]]:
        return [
            [98.0, 100.5, 103.0],
            [98.5, 101.5, 104.0],
            [99.0, 102.5, 104.5],
            [99.0, 103.0, 105.0],
        ]


class FakeTensor:
    def __getitem__(self, index: object) -> FakePathTensor:
        assert index == 0
        return FakePathTensor()


class FakePipeline:
    def predict_quantiles(self, inputs: object, **kwargs: object) -> tuple[list[FakeTensor], None]:
        assert inputs == [[100.0] * 128]
        assert kwargs["prediction_length"] == 4
        assert kwargs["quantile_levels"] == [0.1, 0.5, 0.9]
        return [FakeTensor()], None


class DecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = FixedModel()
        self.market = Market("XAUUSD_l", "M15", 100.0, 100.4, 0.01, bars())

    def test_buy_is_model_led_and_pays_spread(self) -> None:
        result = evaluate(self.market, self.model, Settings(require_direction_confirmation=False, market_state_policy_enabled=False))
        self.assertEqual(result.decision, "BUY")
        self.assertAlmostEqual(result.edge, 2.6)
        self.assertAlmostEqual(result.signal_bid, 100.0)
        self.assertAlmostEqual(result.signal_ask, 100.4)
        self.assertAlmostEqual(result.buy_edge, 2.6)
        self.assertAlmostEqual(result.sell_edge, -3.4)
        self.assertAlmostEqual(result.minimum_edge, 0.6)
        self.assertAlmostEqual(result.uncertainty, 6.0)
        self.assertAlmostEqual(result.signal_strength, 2.6 / 6.0)
        self.assertAlmostEqual(result.minimum_strength, 0.20)
        self.assertEqual(result.signal_bar_time, self.market.bars[-1].time)
        self.assertEqual(self.model.last_close, 100.0)

    def test_sell_and_wait(self) -> None:
        self.model.value = Forecast(95.0, 97.0, 101.0)
        self.assertEqual(evaluate(self.market, self.model, Settings(require_direction_confirmation=False, market_state_policy_enabled=False)).decision, "SELL")
        self.model.value = Forecast(98.0, 100.0, 102.0)
        self.assertEqual(evaluate(self.market, self.model).decision, "WAIT")

    def test_wait_reason_distinguishes_edge_from_strength(self) -> None:
        self.model.value = Forecast(99.0, 100.5, 102.0)
        edge_wait = evaluate(self.market, self.model)
        self.assertEqual(edge_wait.decision, "WAIT")
        self.assertEqual(edge_wait.reason, "insufficient_model_edge")

        self.model.value = Forecast(99.0, 101.2, 105.0)
        strength_wait = evaluate(self.market, self.model)
        self.assertEqual(strength_wait.decision, "WAIT")
        self.assertEqual(strength_wait.reason, "insufficient_model_strength")
        self.assertGreaterEqual(max(strength_wait.buy_edge, strength_wait.sell_edge), strength_wait.minimum_edge)
        self.assertLess(strength_wait.signal_strength, strength_wait.minimum_strength)

    def test_strong_chronos_entry_waits_when_intrabar_move_is_adverse(self) -> None:
        micro = (
            Bar(1_800_100_000, 100.20, 100.25, 100.15, 100.20),
            Bar(1_800_100_060, 100.20, 100.22, 100.08, 100.10),
            Bar(1_800_100_120, 100.10, 100.12, 99.98, 100.02),
            Bar(1_800_100_180, 100.02, 100.04, 99.98, 100.00),
        )
        market = Market("XAUUSD_l", "M15", 100.0, 100.4, 0.01, bars(), micro)

        result = evaluate(market, self.model)

        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.reason, "adverse_intrabar_timing")
        self.assertEqual(result.strong_entry_guard_active, 1)
        self.assertLess(
            result.intrabar_move_atr,
            result.strong_entry_min_intrabar_move_atr,
        )
        self.assertGreaterEqual(result.buy_edge, result.minimum_edge)
        self.assertGreaterEqual(result.signal_strength, result.minimum_strength)

    def test_strong_chronos_entry_allows_small_intrabar_noise(self) -> None:
        micro = (
            Bar(1_800_100_000, 100.02, 100.04, 99.98, 100.02),
            Bar(1_800_100_060, 100.02, 100.03, 99.99, 100.01),
            Bar(1_800_100_120, 100.01, 100.02, 99.99, 100.00),
            Bar(1_800_100_180, 100.00, 100.02, 99.99, 100.00),
        )
        market = Market("XAUUSD_l", "M15", 100.0, 100.4, 0.01, bars(), micro)

        result = evaluate(market, self.model)

        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.reason, "direction_confirmation_required")
        self.assertEqual(result.strong_entry_guard_active, 0)

    def test_weak_intrabar_entry_waits_unless_explicitly_enabled(self) -> None:
        micro = (
            Bar(1_800_100_000, 99.90, 100.00, 99.82, 99.90),
            Bar(1_800_100_060, 99.90, 99.96, 99.78, 99.84),
            Bar(1_800_100_120, 99.84, 99.98, 99.80, 99.95),
            Bar(1_800_100_180, 99.95, 100.03, 99.93, 100.00),
        )
        market = Market("XAUUSD_l", "M15", 100.0, 100.4, 0.01, bars(), micro)
        self.model.value = Forecast(95.0, 101.1, 107.0)

        result = evaluate(market, self.model)

        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.reason, "insufficient_model_strength")
        self.assertEqual(result.intrabar_confirmed, 1)
        result = evaluate(market, self.model, Settings(allow_weak_intrabar_entries=True))

        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.reason, "insufficient_model_strength")
        self.assertEqual(result.intrabar_confirmed, 1)
        self.assertEqual(result.intrabar_direction, "BUY")
        self.assertGreaterEqual(result.buy_edge, result.minimum_edge)
        self.assertLess(result.signal_strength, result.minimum_strength)
        self.assertGreaterEqual(result.signal_strength, result.intrabar_min_strength)
        self.assertGreaterEqual(result.intrabar_move_atr, result.intrabar_min_move_atr)
        self.assertGreaterEqual(result.intrabar_rebound_atr, result.intrabar_min_rebound_atr)

    def test_intrabar_does_not_bypass_model_edge(self) -> None:
        micro = (
            Bar(1_800_100_000, 99.80, 99.90, 99.70, 99.80),
            Bar(1_800_100_060, 99.80, 99.92, 99.72, 99.84),
            Bar(1_800_100_120, 99.84, 100.00, 99.80, 99.95),
            Bar(1_800_100_180, 99.95, 100.05, 99.93, 100.00),
        )
        market = Market("XAUUSD_l", "M15", 100.0, 100.4, 0.01, bars(), micro)
        self.model.value = Forecast(98.0, 100.5, 105.0)

        result = evaluate(market, self.model)

        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.reason, "insufficient_model_edge")
        self.assertEqual(result.intrabar_confirmed, 0)

    def test_weak_sell_intrabar_cannot_fall_through_to_confirmed_trend(self) -> None:
        micro = (
            Bar(1_800_100_000, 100.30, 100.35, 100.25, 100.30),
            Bar(1_800_100_060, 100.30, 100.42, 100.28, 100.38),
            Bar(1_800_100_120, 100.38, 100.40, 100.18, 100.22),
            Bar(1_800_100_180, 100.22, 100.24, 99.98, 100.00),
        )
        market = Market("XAUUSD_l", "M15", 100.0, 100.4, 0.01, bars(), micro)
        self.model.value = Forecast(94.0, 98.9, 106.0, (99.8, 99.6, 99.4, 98.9))
        result = evaluate(market, self.model, Settings(allow_weak_intrabar_entries=False))
        self.assertEqual(result.intrabar_confirmed, 1)
        self.assertEqual(result.ai_trend_confirmed, 1)
        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.reason, "insufficient_model_strength")
        legacy = evaluate(market, self.model, Settings(allow_weak_intrabar_entries=True))
        self.assertEqual(legacy.decision, "SELL")
        self.assertEqual(legacy.reason, "intrabar_reversal_down")
        # Default policy permits aligned weak entries, but rejects chasing.
        aligned = evaluate(market, self.model)
        self.assertEqual(aligned.decision, "SELL")
        late = evaluate(market, self.model, Settings(maximum_entry_extension_atr=0.08))
        self.assertEqual(late.decision, "WAIT")
        self.assertEqual(late.reason, "late_entry_extension")


    def test_ai_trend_continuation_uses_chronos_path_not_indicators(self) -> None:
        micro = (
            Bar(1_800_100_000, 100.30, 100.35, 100.25, 100.30),
            Bar(1_800_100_060, 100.30, 100.42, 100.28, 100.38),
            Bar(1_800_100_120, 100.38, 100.40, 100.18, 100.22),
            Bar(1_800_100_180, 100.22, 100.24, 99.98, 100.00),
        )
        market = Market("XAUUSD_l", "M15", 100.0, 100.4, 0.01, bars(), micro)
        self.model.value = Forecast(
            94.0,
            99.3,
            106.0,
            (99.8, 99.6, 99.4, 99.3),
        )

        result = evaluate(market, self.model)

        self.assertEqual(result.ai_trend_direction, "SELL")
        self.assertEqual(result.ai_trend_confirmed, 1)
        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.reason, "insufficient_model_edge")
        self.assertLess(result.sell_edge, result.minimum_edge)
        self.assertGreaterEqual(
            result.sell_edge,
            result.minimum_edge * result.trend_min_edge_fraction,
        )
        self.assertGreaterEqual(result.ai_trend_move_atr, result.trend_min_path_atr)
        self.assertGreaterEqual(result.ai_trend_consistency, result.trend_min_consistency)

    def test_ai_trend_continuation_requires_chronos_path(self) -> None:
        micro = (
            Bar(1_800_100_000, 100.30, 100.35, 100.25, 100.30),
            Bar(1_800_100_060, 100.30, 100.42, 100.28, 100.38),
            Bar(1_800_100_120, 100.38, 100.40, 100.18, 100.22),
            Bar(1_800_100_180, 100.22, 100.24, 99.98, 100.00),
        )
        market = Market("XAUUSD_l", "M15", 100.0, 100.4, 0.01, bars(), micro)
        self.model.value = Forecast(94.0, 99.3, 106.0)

        result = evaluate(market, self.model)

        self.assertEqual(result.ai_trend_direction, "NONE")
        self.assertEqual(result.ai_trend_confirmed, 0)
        self.assertEqual(result.decision, "WAIT")

    def test_spread_failure_does_not_run_model(self) -> None:
        market = Market("XAUUSD_l", "M15", 100.0, 101.0, 0.01, bars())
        result = evaluate(market, self.model)
        self.assertEqual(result.decision, "WAIT")
        self.assertEqual(result.reason, "spread_or_atr")
        self.assertEqual(self.model.calls, 0)

    def test_chronos_adapter_reads_final_horizon_quantiles(self) -> None:
        class FakeNumpy:
            float32 = "float32"

            def asarray(self, data: list[float], dtype: str) -> list[float]:
                self_dtype = self.float32
                assert dtype == self_dtype
                return list(data)

        adapter = ChronosForecaster.__new__(ChronosForecaster)
        adapter._np = FakeNumpy()
        adapter.pipeline = FakePipeline()
        self.assertEqual(
            adapter.forecast([100.0] * 128, 4),
            Forecast(99.0, 103.0, 105.0, (100.5, 101.5, 102.5, 103.0)),
        )

    def test_bad_history_and_bad_forecast_fail_closed(self) -> None:
        payload = {
            "symbol": "XAUUSD_l", "timeframe": "M15", "bid": 100, "ask": 100.4,
            "point": 0.01, "bars": [dict(time=b.time, open=b.open, high=b.high, low=b.low, close=b.close) for b in bars()],
        }
        payload["bars"][-1]["time"] = payload["bars"][-2]["time"]
        with self.assertRaises(ValueError):
            Market.from_dict(payload)
        self.model.value = Forecast(float("nan"), 100.0, 101.0)
        with self.assertRaises(ValueError):
            evaluate(self.market, self.model)

    def test_replay_enters_next_bar_and_counts_ambiguous_stop_first(self) -> None:
        history = list(bars(262))
        history[257] = Bar(history[257].time, 100.0, 105.0, 98.0, 100.0)
        result = replay(history, [40] * 262, self.model, start=256, settings=Settings(horizon=4, require_direction_confirmation=False, market_state_policy_enabled=False))
        self.assertEqual((result.buys, result.wins, result.losses), (1, 0, 1))
        self.assertEqual(result.net_r, -1.0)

    def test_history_persistence_failure_does_not_fail_decision_path(self) -> None:
        from unittest.mock import patch

        with patch("ramon.server.persist_market", side_effect=PermissionError("read-only")):
            error = persist_market_safely("/data/ramon_history.sqlite3", self.market)
        self.assertIn("PermissionError", error)
        self.assertIn("read-only", error)

    def test_cached_forecaster_reuses_same_completed_m15_context(self) -> None:
        base = FixedModel(Forecast(99.0, 103.0, 105.0))
        cached = CachedForecaster(base)
        closes = [100.0] * 256

        first = cached.forecast(closes, 4)
        second = cached.forecast(list(closes), 4)
        self.assertEqual(first, second)
        self.assertEqual(base.calls, 1)

        changed = list(closes)
        changed[-1] = 100.1
        cached.forecast(changed, 4)
        self.assertEqual(base.calls, 2)

    def test_http_round_trip_and_model_failure(self) -> None:
        with socket.socket() as temporary:
            temporary.bind(("127.0.0.1", 0))
            port = temporary.getsockname()[1]
        worker = threading.Thread(
            target=serve, args=("127.0.0.1", port, self.model, Settings(require_direction_confirmation=False, market_state_policy_enabled=False)), daemon=True
        )
        worker.start()
        url = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                with urlopen(url + "/health", timeout=1) as response:
                    self.assertTrue(json.load(response)["ready"])
                break
            except OSError:
                time.sleep(0.02)
        else:
            self.fail("service did not start")
        payload = json.dumps({
            "symbol": "XAUUSD_l", "timeframe": "M15", "bid": 100, "ask": 100.4,
            "point": 0.01, "bars": [dict(time=b.time, open=b.open, high=b.high, low=b.low, close=b.close) for b in bars()],
        }).encode()
        with urlopen(Request(url + "/decision", data=payload, headers={"Content-Type": "application/json"}), timeout=5) as response:
            body = json.load(response)
            self.assertEqual(body["decision"], "BUY")
            self.assertEqual(body["signal_bid"], 100.0)
            self.assertEqual(body["signal_ask"], 100.4)
            self.assertIn("buy_edge", body)
            self.assertIn("signal_strength", body)
            self.assertEqual(body["minimum_strength"], 0.20)
        self.model.value = Forecast(float("nan"), 100.0, 101.0)
        changed_payload = json.loads(payload)
        changed_payload["bars"][-1]["open"] = 100.1
        changed_payload["bars"][-1]["high"] = 100.6
        changed_payload["bars"][-1]["low"] = 99.6
        changed_payload["bars"][-1]["close"] = 100.1
        with self.assertRaises(HTTPError) as raised:
            urlopen(
                Request(
                    url + "/decision",
                    data=json.dumps(changed_payload).encode(),
                    headers={"Content-Type": "application/json"},
                ),
                timeout=5,
            )
        self.assertEqual(raised.exception.code, 400)


if __name__ == "__main__":
    unittest.main()


def test_last_losing_buy_without_confirmation_is_rejected():
    # Recorded entry: BUY strength .316, intrabar false, model path SELL.
    history=tuple(Bar(1800000000+i*900,4160.86,4169.28,4152.44,4160.86) for i in range(256))
    micro=tuple(Bar(1800300000+i*60,v,v+.1,v-.1,v) for i,v in enumerate((4147.49,4147.43,4147.43)))
    market=Market('XAUUSD_l','M15',4147.43,4147.85,.01,history,micro)
    model=FixedModel(Forecast(4143.54736328125,4156.888671875,4172.171875,
                              (4160,4159,4158,4156.888671875)))
    result=evaluate(market,model)
    assert result.signal_strength > .3
    assert not result.intrabar_confirmed
    assert result.ai_trend_direction=='SELL'
    assert result.decision=='WAIT'
    assert result.reason=='direction_confirmation_required'


def test_confirmed_strong_buy_remains_allowed():
    micro=tuple(Bar(1800300000+i*60,v,v+.1,v-.1,v) for i,v in enumerate((99.8,99.7,99.9,100)))
    market=Market('XAUUSD_l','M15',100,100.4,.01,bars(),micro)
    result=evaluate(market,FixedModel(Forecast(99,103,105,(100.5,101,102,103))))
    assert result.decision=='BUY'
    assert result.intrabar_confirmed and result.ai_trend_confirmed
