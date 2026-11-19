"""Tests of descriptive SELL research; no live trading logic involved."""
from django.test import SimpleTestCase

from .forensic_research import analyze_sell_research


def row(trade_id, pnl, *, side="SELL", regime="BEARISH",
        ema="BEAR", chase=0.1, status="VERIFIED_EVALUATION", closed="2026-11-18T10:00:00Z"):
    return dict(trade_id=trade_id, closed_at=closed, pnl=pnl, side=side,
                regime=regime, ema_bias=ema, chase_atr=chase,
                evidence_status=status)


class SellResearchTests(SimpleTestCase):
    def test_winning_and_losing_sell_counts_and_flags(self):
        rows = [row("loss", -10, regime="BULLISH", ema="BULL", chase=.6),
                row("win", 15), row("buy", -4, side="BUY")]
        result = analyze_sell_research(rows)
        self.assertEqual(result["summary"]["all_sell"]["trades"], 2)
        self.assertEqual(result["summary"]["sell_losers"]["trades"], 1)
        self.assertEqual(result["summary"]["sell_winners"]["trades"], 1)
        flags = {x["flag"]: x for x in result["evidence_flags"]}
        self.assertEqual(flags["SELL_AGAINST_BULLISH_REGIME"]["losses"], 1)
        self.assertEqual(flags["EXTENDED_ENTRY_OVER_035_ATR"]["trades"], 1)
        self.assertEqual(flags["SELL_AGAINST_BULLISH_EMA"]["trades"], 1)

    def test_unknown_evidence_never_assumed_safe_or_verified(self):
        result = analyze_sell_research([row("old", -5, status="UNLINKED"),
                                        row("missing", 3, chase=None)])
        self.assertEqual(result["summary"]["unverified_sell_count"], 1)
        self.assertEqual(result["verified_sample_size"], 1)
        flags = {x["flag"] for x in result["evidence_flags"]}
        self.assertIn("ENTRY_EVIDENCE_UNVERIFIED", flags)
        self.assertIn("CHASE_EVIDENCE_MISSING", flags)

    def test_chronological_split_replays_only_verified_fills(self):
        rows = [
            row(str(i), -10 if i%2 else 15, regime="BULLISH" if i%2 else "BEARISH",
                closed=f"2026-11-18T{i:02d}:00:00Z") for i in range(10)
        ]
        result = analyze_sell_research(rows)
        base = next(v for v in result["variants"] if v["key"] == "baseline")
        bearish = next(v for v in result["variants"] if v["key"] == "bearish_regime")
        self.assertEqual([x["eligible"] for x in base["splits"]], [7, 3])
        self.assertEqual([x["retained"] for x in base["splits"]], [7, 3])
        self.assertEqual([x["retained"] for x in bearish["splits"]], [4, 1])
        self.assertFalse(result["research_ready"])

    def test_missing_chase_excludes_strict_filter(self):
        result = analyze_sell_research([row("a", 5, chase=None),
                                        row("b", -10, chase=.5),
                                        row("c", 8, chase=.1)])
        strict = next(v for v in result["variants"] if v["key"]=="bearish_regime_ema_no_chase")
        self.assertEqual(sum(x["retained"] for x in strict["splits"]), 1)

    def test_truncation_never_marks_as_research_ready(self):
        rows = [row(str(i), 5, closed=f"2026-11-18T{i%24:02d}:00:00Z") for i in range(35)]
        result = analyze_sell_research(rows,truncated=True)
        self.assertFalse(result["research_ready"])
        self.assertEqual(result["verified_sample_size"], 35)
        self.assertEqual(result["method"],"HISTORICAL_FILLED_TRADE_FILTER_REPLAY")
