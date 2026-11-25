from django.test import SimpleTestCase
from ramon_pipeline.signalflow.opportunities import OpportunityConfig, _parse_bars, InvalidMarketData


class M5OpportunityCadenceTests(SimpleTestCase):
    def bars(self, interval):
        return [dict(time=1800000000+i*interval, high=101, low=99,
                     close=100, open=100) for i in range(130)]

    def test_m5_complete_history(self):
        parsed = _parse_bars(self.bars(300), OpportunityConfig(), bar_seconds=300)
        self.assertEqual(len(parsed.times), 130)

    def test_m5_large_recent_gap(self):
        rows = self.bars(300)
        rows[-1]["time"] += 3000
        with self.assertRaises(InvalidMarketData):
            _parse_bars(rows, OpportunityConfig(), bar_seconds=300)

    def test_m15_existing_call(self):
        parsed = _parse_bars(self.bars(900), OpportunityConfig())
        self.assertEqual(len(parsed.times), 130)
