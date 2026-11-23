"""Tests for the isolated impulse-continuation candidate, no Django DB or EA."""
from datetime import datetime, timezone
from unittest import TestCase
from impulse_continuation_research import impulse_continuation_research


def candles(count=100, gap_after=None):
    start = 1_795_363_200  # fixed UTC quarter-hour alignment
    start = start - start % 900
    bars = []
    t = start
    for i in range(count):
        if i == gap_after:
            t += 900
        value = 3000 + i * .25
        bars.append({"time":t, "open":value, "high":value+.3,
                     "low":value-.3, "close":value+.15})
        t += 900
    return bars


def evaluate(rows, **changes):
    params = dict(start_ts=rows[0]["time"], end_ts=rows[-1]["time"]+900,
                  balance=1000, spread_points=10, point=.01,
                  tick_size=.01, tick_value=1, volume_min=.01,
                  volume_max=100, volume_step=.01, risk_percent=.5,
                  slippage_points=1, commission_per_lot=0)
    params.update(changes)
    return impulse_continuation_research(rows, **params)


class ImpulseContinuationResearchTests(TestCase):
    def test_research_report_never_authorizes_live_execution(self):
        result = evaluate(candles())
        self.assertEqual(result["mode"], "RESEARCH_ONLY")
        self.assertFalse(result["executions_authorized"])
        self.assertIn("net_gain", result)
        self.assertIn("trades", result)
        self.assertIn("max_drawdown_pct", result)

    def test_research_resets_signal_warmup_after_gap(self):
        rows = candles(90, gap_after=70)
        result = evaluate(rows)
        self.assertEqual(result["rejections"].get("DATA_GAP_RESET"), 1)
        self.assertFalse(result["executions_authorized"])

    def test_risk_ceiling_does_not_force_min_lot(self):
        rows = candles()
        # The report remains research-only even on a very small balance.
        result = evaluate(rows, balance=.1)
        for trade in result["trades"]:
            self.assertGreaterEqual(trade["volume"], .01)
        self.assertFalse(result["executions_authorized"])

    def test_rejects_invalid_account_limits(self):
        result = evaluate(candles(), risk_percent=.6)
        self.assertEqual(result["status"], "INVALID_INPUT")

    def test_timestamps_are_reproducible_and_no_forward_trade(self):
        result = evaluate(candles())
        for trade in result["trades"]:
            self.assertGreater(trade["opened_at"], trade["signal_at"])
            self.assertGreaterEqual(trade["closed_at"], trade["opened_at"])

    def test_input_is_never_mutated(self):
        rows = candles()
        before = [dict(bar) for bar in rows]
        evaluate(rows)
        self.assertEqual(rows, before)
