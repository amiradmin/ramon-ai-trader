from datetime import datetime, timezone
from unittest import TestCase

from ramon_pipeline.signalflow import common, entry_timing, opportunities, risk_management
from ramon_pipeline.signalflow.common import InvalidMarketData, clip, finite_number, parse_timestamp


class SharedHelperTests(TestCase):
    def test_every_stage_uses_the_same_status_vocabulary(self):
        for module in (opportunities, entry_timing, risk_management):
            self.assertIs(module.ACCEPTED, common.ACCEPTED)
            self.assertIs(module.REJECTED, common.REJECTED)
            self.assertIs(module.DEFERRED, common.DEFERRED)
        self.assertEqual((common.ACCEPTED, common.REJECTED, common.DEFERRED), ("ACCEPTED", "REJECTED", "DEFERRED"))

    def test_finite_number(self):
        self.assertEqual(finite_number("2.5", "x"), 2.5)
        self.assertEqual(finite_number(3, "x"), 3.0)
        for bad in (True, None, "abc", float("nan"), float("inf"), [1]):
            with self.assertRaises(InvalidMarketData) as caught:
                finite_number(bad, "price")
            self.assertEqual(caught.exception.reason, "INVALID_MARKET_DATA")
            self.assertIn("price", caught.exception.detail)

    def test_parse_timestamp_accepts_epoch_datetime_and_iso(self):
        expected = 1_700_000_000.0
        moment = datetime.fromtimestamp(expected, tz=timezone.utc)
        self.assertEqual(parse_timestamp(1_700_000_000), expected)
        self.assertEqual(parse_timestamp("1700000000"), expected)
        self.assertEqual(parse_timestamp(moment), expected)
        self.assertEqual(parse_timestamp(moment.isoformat()), expected)
        self.assertEqual(parse_timestamp(moment.replace(tzinfo=None)), expected)  # naive means UTC
        self.assertEqual(parse_timestamp("2023-11-14T22:13:20Z"), expected)

    def test_parse_timestamp_rejects_garbage(self):
        for bad in ("yesterday", None, float("nan"), True, {}):
            with self.assertRaises(InvalidMarketData):
                parse_timestamp(bad)

    def test_clip(self):
        self.assertEqual((clip(-1), clip(0.4), clip(7)), (0.0, 0.4, 1.0))
        self.assertEqual(clip(5, 1, 3), 3)
