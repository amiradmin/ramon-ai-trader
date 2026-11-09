from datetime import timedelta
from decimal import Decimal
import json
import time
from unittest.mock import patch
from io import StringIO

from django.test import TestCase, override_settings
from django.core.management import call_command
from django.utils import timezone

from ramon_pipeline.signalflow.tests.test_opportunities import LONG_SETUP, make_bars
from .decision import _build_pipeline
from .market_context import load_decision_context
from .models import EACommand, EAHeartbeat, MarketBar, RiskVerdictRecord, StrategyDecisionRecord


class ContextLoadingTests(TestCase):
    def test_context_is_scoped_completed_and_known_at_decision_time(self):
        cutoff = timezone.now()+timedelta(seconds=5)
        common = {"source": "demo", "symbol": "XAUUSD_o", "timeframe": "M5",
                  "timestamp": cutoff-timedelta(minutes=5), "open": 100, "high": 101, "low": 99, "close": 100}
        valid = MarketBar.objects.create(**common)
        MarketBar.objects.create(**{**common, "source": "other"})
        MarketBar.objects.create(**{**common, "symbol": "OTHER"})
        MarketBar.objects.create(**{**common, "timeframe": "M15"})
        MarketBar.objects.create(**{**common, "timestamp": cutoff-timedelta(minutes=4)})
        late = MarketBar.objects.create(**{**common, "timestamp": cutoff-timedelta(minutes=10)})
        MarketBar.objects.filter(pk=late.pk).update(received_at=cutoff+timedelta(seconds=1))
        context = load_decision_context(source="demo", symbol="XAUUSD_o", decision_time=cutoff.timestamp())
        self.assertEqual(len(context["M5"]), 1)
        self.assertEqual(context["M5"][0]["time"], valid.timestamp.timestamp())
        self.assertEqual(context["H1"], [])


class DemoPlaybookApiTests(TestCase):
    @override_settings(RAMON_DEMO_PROFILE="expanded")
    def test_comparison_and_computed_quality_are_demo_specific(self):
        demo, live = _build_pipeline(demo_mode=True), _build_pipeline(demo_mode=False)
        self.assertTrue(demo.compare_candidates)
        self.assertTrue(demo.entry_timer.config.ignore_supplied_quality)
        self.assertFalse(live.compare_candidates)
        self.assertFalse(live.entry_timer.config.ignore_supplied_quality)

    @override_settings(RAMON_DEMO_COMPARE_CANDIDATES=False)
    def test_comparison_can_be_rolled_back_without_changing_execution_settings(self):
        self.assertFalse(_build_pipeline(demo_mode=True).compare_candidates)

    def payload(self):
        bars = make_bars(LONG_SETUP)
        now = int(time.time())
        shift = now-900-bars[-1]["time"]
        for bar in bars:
            bar["time"] += shift
        bid, ask = bars[-1]["close"], bars[-1]["close"]+0.1
        EAHeartbeat.objects.create(instance_id="playbook-demo", account_login="123", server_name="Demo",
            symbol="XAUUSD_o", status="TELEMETRY_OK", terminal_connected=True, trade_allowed=True,
            payload={"server_name": "Demo", "account_trade_mode": 0,
                     "account": {"balance": "10000", "equity": "10000"}, "positions": [],
                     "quote": {"bid": bid, "ask": ask, "point": 0.01},
                     "quote_time": timezone.now().isoformat(),
                     "symbol_info": {"tick_size": "0.01", "tick_value": "1", "volume_min": "0.01",
                                     "volume_max": "100", "volume_step": "0.01", "stops_level_points": "0"}})
        payload = {"instance_id": "playbook-demo", "account_login": "123", "server_name": "Demo",
                   "symbol": "XAUUSD_o", "timeframe": "M15", "bars": bars, "bid": bid, "ask": ask, "point": 0.01}
        return payload

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_MARKET_SESSION_HOURS=None, RAMON_MARKET_SESSION_TIMEZONE=None,
                       RAMON_EA_COMMAND_CREATION_ENABLED=False, RAMON_EA_COMMAND_EXECUTION_ENABLED=False)
    def test_api_persists_selection_and_rejects_a_plan_without_room_to_target(self):
        payload = self.payload()
        response = self.client.post("/api/ea/v1/decision", json.dumps(payload), content_type="application/json",
                                    HTTP_AUTHORIZATION="Bearer test-token")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(len(body["selection"]["candidates"]), 7)
        self.assertEqual(body["selection"]["context"]["H1"]["status"], "MISSING")
        decision = StrategyDecisionRecord.objects.get(decision_id=body["sample_key"])
        self.assertEqual(decision.features["selection"], body["selection"])
        self.assertEqual(body["stages"]["entry_timing"]["quality_source"], "COMPUTED")
        verdict = RiskVerdictRecord.objects.get(decision_id=body["sample_key"])
        self.assertFalse(verdict.approved)
        self.assertEqual(body["reason"], "TARGET_ROOM_TOO_SMALL")
        self.assertEqual(body["decision"], "WAIT")
        self.assertFalse(EACommand.objects.exists())

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_EA_COMMAND_CREATION_ENABLED=False,
                       RAMON_EA_COMMAND_EXECUTION_ENABLED=False)
    @patch("ea_bridge.decision.SignalFlowPipeline.run")
    def test_api_uses_the_selected_structural_target_instead_of_rebuilding_a_fixed_r_target(self, run):
        payload = self.payload()
        entry = payload["ask"]
        planned = entry+4.0
        run.return_value = {"approved": True, "action": "BUY", "reason": "SIGNALFLOW_APPROVED",
            "selection": {"version": "demo-playbook-v1"}, "stages": {
                "opportunities": {"approved": True, "score": 0.8, "checks": [], "setup": {"type": "BREAKOUT_RETEST"}},
                "entry_timing": {"approved": True, "checks": []},
                "risk_management": {"approved": True, "volume": 0.1, "stop_price": entry-2.0,
                                    "target_price": planned, "target_reward_risk": 2.0, "checks": []}}}
        response = self.client.post("/api/ea/v1/decision", json.dumps(payload), content_type="application/json",
                                    HTTP_AUTHORIZATION="Bearer test-token")
        self.assertEqual(response.status_code, 200, response.content)
        verdict = RiskVerdictRecord.objects.get(decision_id=response.json()["sample_key"])
        self.assertTrue(verdict.approved, response.json())
        self.assertAlmostEqual(float(verdict.take_profit), planned)
        self.assertGreater(verdict.take_profit, Decimal(str(entry)))
        self.assertFalse(EACommand.objects.exists())


class ReadOnlyReplayCommandTests(TestCase):
    def test_command_excludes_off_grid_history_and_writes_no_trading_records(self):
        EAHeartbeat.objects.create(instance_id="replay-demo", symbol="XAUUSD_o", payload={
            "account_trade_mode": 0, "quote": {"point": "0.01"},
            "symbol_info": {"tick_size": "0.01", "tick_value": "1", "volume_min": "0.01",
                            "volume_max": "100", "volume_step": "0.01", "stops_level_points": "0"}})
        end = int(time.time())//900*900-900
        common = {"source": "replay-demo", "symbol": "XAUUSD_o", "timeframe": "M15",
                  "open": 100, "high": 100, "low": 100, "close": 100}
        MarketBar.objects.bulk_create([MarketBar(**common,
            timestamp=timezone.datetime.fromtimestamp(end-i*900, tz=timezone.get_default_timezone())) for i in range(130)])
        MarketBar.objects.create(**common,
            timestamp=timezone.datetime.fromtimestamp(end-1, tz=timezone.get_default_timezone()))
        counts = (MarketBar.objects.count(), StrategyDecisionRecord.objects.count(),
                  RiskVerdictRecord.objects.count(), EACommand.objects.count())
        output = StringIO()
        call_command("evaluate_demo_playbook", source="replay-demo", symbol="XAUUSD_o", spread_points=50,
                     slippage_points=5, commission_points=0, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["data_quality"]["off_grid_rows_excluded"], 1)
        self.assertEqual(report["m15_bars"], 130)
        self.assertEqual(report["baseline"]["trade_count"], 0)
        self.assertEqual(report["playbook"]["trade_count"], 0)
        self.assertEqual(counts, (MarketBar.objects.count(), StrategyDecisionRecord.objects.count(),
                                  RiskVerdictRecord.objects.count(), EACommand.objects.count()))
