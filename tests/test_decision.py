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
from ramon.server import serve
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


class FakeTensor:
    def __getitem__(self, indices: object) -> FakeTensor:
        assert indices == (0, -1, slice(None))
        return self

    def tolist(self) -> list[float]:
        return [99.0, 103.0, 105.0]


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
        result = evaluate(self.market, self.model)
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
        self.assertEqual(evaluate(self.market, self.model).decision, "SELL")
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
        self.assertEqual(adapter.forecast([100.0] * 128, 4), Forecast(99.0, 103.0, 105.0))

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
        result = replay(history, [40] * 262, self.model, start=256, settings=Settings(horizon=4))
        self.assertEqual((result.buys, result.wins, result.losses), (1, 0, 1))
        self.assertEqual(result.net_r, -1.0)

    def test_http_round_trip_and_model_failure(self) -> None:
        with socket.socket() as temporary:
            temporary.bind(("127.0.0.1", 0))
            port = temporary.getsockname()[1]
        worker = threading.Thread(
            target=serve, args=("127.0.0.1", port, self.model, Settings()), daemon=True
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
        with self.assertRaises(HTTPError) as raised:
            urlopen(Request(url + "/decision", data=payload), timeout=5)
        self.assertEqual(raised.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
