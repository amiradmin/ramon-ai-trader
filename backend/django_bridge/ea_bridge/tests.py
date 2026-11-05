import json
import time
from decimal import Decimal
from datetime import date, timedelta
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import AccountDailyBaseline, EACommand, EAEvent, EAHeartbeat, MarketBar, RiskVerdictRecord, StrategyDecisionRecord, DecisionThresholdConfig
from ramon_pipeline.management.commands.consume_ramon_streams import Command




class DecisionThresholdConfigurationTests(TestCase):
    url = "/api/ea/v1/decision-thresholds"

    def setUp(self):
        EAHeartbeat.objects.create(
            instance_id="threshold-demo-ea",
            account_login="demo-123",
            server_name="LiteFinance-MT5-Demo",
            payload={"account_trade_mode": "DEMO"},
        )

    def test_get_returns_editable_groups_for_telemetry_selected_demo_profile(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["profile"], "demo")
        groups = {group["key"]: group for group in data["groups"]}
        volatility = {item["key"]: item for item in groups["opportunities"]["items"]}
        self.assertEqual(volatility["atr_ratio_min"]["value"], 0.6)
        self.assertEqual(volatility["atr_ratio_max"]["value"], 2.5)
        risk_items = {item["key"]: item for item in groups["risk_management"]["items"]}
        self.assertEqual(risk_items["min_reward_risk"]["value"], 1.2)
        self.assertEqual(risk_items["min_reward_risk"]["min"], 1.2)
        self.assertEqual(risk_items["min_reward_risk"]["max"], 3.0)
        self.assertEqual(risk_items["maximum_risk_percent"]["value"], 0.5)
        self.assertEqual(risk_items["max_total_risk_percent"]["value"], 2.0)
        self.assertEqual(risk_items["target_reward_risk"]["value"], 1.25)
        self.assertIn("entry_timing", groups)
        self.assertIn("risk_management", groups)

    def test_saved_thresholds_are_applied_to_the_next_django_pipeline(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"values": {
                "opportunities": {"atr_ratio_min": 0.75, "atr_ratio_max": 2.2},
                "entry_timing": {"max_chase_atr": 0.7, "minimum_quality": 0.6, "maximum_spread_points": 90},
                "risk_management": {"maximum_risk_percent": 0.75, "max_total_risk_percent": 2.5, "target_reward_risk": 1.5, "min_reward_risk": 1.2},
            }}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        from .decision import _build_pipeline
        pipeline = _build_pipeline(demo_mode=True)
        self.assertEqual(pipeline.opportunities.config.atr_ratio_min, 0.75)
        self.assertEqual(pipeline.opportunities.config.atr_ratio_max, 2.2)
        self.assertEqual(pipeline.entry_timer.config.max_chase_atr, 0.7)
        self.assertEqual(pipeline.entry_timer.minimum_quality, 0.6)
        self.assertEqual(pipeline.entry_timer.maximum_spread_points, 90)
        self.assertEqual(pipeline.risk_manager.config.min_reward_risk, 1.2)
        self.assertEqual(pipeline.risk_manager.config.target_reward_risk, 1.5)
        self.assertEqual(pipeline.risk_manager.maximum_risk_percent, 0.75)
        self.assertEqual(pipeline.risk_manager.config.max_total_risk_percent, 2.5)

    def test_rejects_invalid_or_non_strategy_thresholds(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"values": {
                "opportunities": {"atr_ratio_min": 3.0, "atr_ratio_max": 2.0},
            }}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            self.url,
            data=json.dumps({"values": {
                "risk_management": {"daily_loss_percent": 99},
            }}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_risk_relationships_are_validated_together(self):
        for updates in (
            {"maximum_risk_percent": 1.5, "max_total_risk_percent": 1.0},
            {"target_reward_risk": 1.2, "min_reward_risk": 1.5},
        ):
            response = self.client.post(
                self.url,
                data=json.dumps({"values": {"risk_management": updates}}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400)

    def test_minimum_reward_risk_is_limited_to_1_2_to_three_r(self):
        for value in (1.1, 3.1):
            response = self.client.post(
                self.url,
                data=json.dumps({"values": {"risk_management": {"min_reward_risk": value}}}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400)

        response = self.client.post(
            self.url,
            data=json.dumps({"values": {"risk_management": {"min_reward_risk": 1.8, "target_reward_risk": 1.8}}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["values"]["risk_management"]["min_reward_risk"], 1.8)

    def test_reset_restores_profile_defaults(self):
        self.client.post(
            self.url,
            data=json.dumps({"values": {"opportunities": {"atr_ratio_min": 0.8}}}),
            content_type="application/json",
        )
        response = self.client.post(
            self.url, data=json.dumps({"action": "reset"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["values"]["opportunities"]["atr_ratio_min"], 0.6)
        self.assertEqual(DecisionThresholdConfig.objects.get(profile="demo").values, {})

class DemoOpportunityProfileTests(TestCase):
    def test_expanded_opportunity_profile_is_demo_only_and_keeps_risk_limits(self):
        from .decision import _build_pipeline

        demo = _build_pipeline(demo_mode=True)
        standard = _build_pipeline(demo_mode=False)

        self.assertEqual(demo.opportunities.minimum_score, 0.42)
        self.assertEqual(demo.opportunities.config.adx_trending, 10.0)
        self.assertEqual(demo.opportunities.config.adx_ranging, 8.0)
        self.assertEqual(demo.opportunities.config.min_ema_separation_atr, 0.06)
        self.assertEqual(demo.opportunities.config.support_tolerance_atr, 1.00)
        self.assertEqual(demo.opportunities.config.min_impulse_atr, 0.80)
        self.assertEqual(demo.opportunities.config.min_retracement, 0.05)
        self.assertEqual(demo.opportunities.config.max_retracement, 0.95)
        self.assertEqual(demo.opportunities.config.zone_tolerance_atr, 0.90)
        self.assertEqual(demo.opportunities.config.max_bars_since_pullback_low, 16)
        self.assertEqual(demo.opportunities.config.rsi_overbought, 85.0)
        self.assertEqual(demo.opportunities.config.breakout_lookback, 12)
        self.assertEqual(demo.opportunities.config.breakout_buffer_atr, 0.00)
        self.assertEqual(demo.opportunities.config.breakout_retest_tolerance_atr, 0.50)
        self.assertEqual(demo.opportunities.config.breakout_max_retest_bars, 6)
        self.assertEqual(demo.entry_timer.minimum_quality, 0.42)
        self.assertEqual(demo.entry_timer.maximum_spread_points, 100.0)

        self.assertEqual(standard.opportunities.minimum_score, 0.55)
        self.assertEqual(standard.opportunities.config.adx_trending, 12.0)
        self.assertEqual(standard.entry_timer.minimum_quality, 0.55)

        # Discovery is relaxed; account and portfolio risk remain unchanged.
        self.assertEqual(demo.risk_manager.maximum_risk_percent, 0.5)
        self.assertEqual(demo.risk_manager.config.max_total_risk_percent, 2.0)
        self.assertEqual(demo.risk_manager.config.min_reward_risk, 1.2)


    @override_settings(RAMON_DEMO_PROFILE="standard")
    def test_standard_demo_profile_restores_live_discovery_thresholds(self):
        from .decision import _build_pipeline

        demo = _build_pipeline(demo_mode=True)

        self.assertEqual(demo.opportunities.minimum_score, 0.55)
        self.assertEqual(demo.opportunities.config.min_ema_separation_atr, 0.20)
        self.assertEqual(demo.opportunities.config.support_tolerance_atr, 0.65)
        self.assertEqual(demo.opportunities.config.min_impulse_atr, 1.5)
        self.assertEqual(demo.entry_timer.minimum_quality, 0.55)
        self.assertEqual(demo.entry_timer.maximum_spread_points, 80.0)

        # The profile switch must not change the risk manager's hard limits.
        self.assertEqual(demo.risk_manager.maximum_risk_percent, 0.5)
        self.assertEqual(demo.risk_manager.config.max_total_risk_percent, 2.0)
        self.assertEqual(demo.risk_manager.config.min_reward_risk, 1.2)


class EAIngestionTests(TestCase):
    def setUp(self):
        self.url = "/api/ea/v1/events"
        self.headers = {"HTTP_AUTHORIZATION": "Bearer test-token"}
        self.payload = {"instance_id": "ea-test-1", "event_id": "evt-test-1", "event_type": "EXECUTION_FILL",
                        "occurred_at": "2026-10-22T10:00:00Z", "ticket": "123"}

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_PIPELINE_INGEST_ENABLED=False)
    def test_legacy_durable_ingestion_remains_default(self):
        response = self.client.post(self.url, data=json.dumps(self.payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["accepted"])
        self.assertTrue(EAEvent.objects.filter(event_id="evt-test-1").exists())

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_PIPELINE_INGEST_ENABLED=True)
    @patch("ramon_pipeline.django_adapter.build_pipeline")
    def test_opt_in_pipeline_queues_without_synchronous_event_insert(self, build_pipeline):
        pipeline = Mock()
        pipeline.accept.return_value = "1710000000000-0"
        build_pipeline.return_value = pipeline
        response = self.client.post(self.url, data=json.dumps(self.payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["queued"])
        pipeline.accept.assert_called_once()
        self.assertFalse(EAEvent.objects.filter(event_id="evt-test-1").exists())

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_PIPELINE_INGEST_ENABLED=True)
    @patch("ramon_pipeline.django_adapter.build_pipeline", side_effect=ConnectionError("redis unavailable"))
    def test_pipeline_returns_retryable_error_when_redis_is_unavailable(self, _build_pipeline):
        response = self.client.post(self.url, data=json.dumps(self.payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "2")
        self.assertFalse(EAEvent.objects.filter(event_id="evt-test-1").exists())

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_rejects_timezone_naive_event_timestamp(self):
        payload = dict(self.payload, occurred_at="2026-10-22T10:00:00")
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 400)

    @override_settings(EA_BEARER_TOKEN="")
    def test_rejects_when_token_is_not_configured(self):
        response = self.client.post(self.url, data=json.dumps(self.payload), content_type="application/json")
        self.assertEqual(response.status_code, 503)


class StreamPersistenceTests(TestCase):
    def setUp(self):
        self.worker = Command()

    def test_execution_event_persistence_is_idempotent(self):
        record = {"kind": "execution_event", "source": "ea-test-1", "event_id": "fill-001",
                  "occurred_at": "2026-10-22T10:00:00+00:00",
                  "payload": json.dumps({"ticket": "123", "profit": 0.15})}
        self.worker._persist(record)
        self.worker._persist(record)
        self.assertEqual(EAEvent.objects.filter(event_id="fill-001").count(), 1)

    def test_execution_event_rejects_timezone_naive_timestamp(self):
        record = {
            "kind": "execution_event",
            "source": "ea-test-1",
            "event_id": "fill-naive-001",
            "occurred_at": "2026-10-22T10:00:00",
            "payload": json.dumps({"ticket": "123", "profit": 0.15}),
        }
        with self.assertRaisesRegex(ValueError, "occurred_at must include a timezone"):
            self.worker._persist(record)
        self.assertFalse(EAEvent.objects.filter(event_id="fill-naive-001").exists())

    def test_execution_event_rejects_invalid_timestamp(self):
        record = {
            "kind": "execution_event",
            "source": "ea-test-1",
            "event_id": "fill-invalid-001",
            "occurred_at": "not-a-timestamp",
            "payload": json.dumps({"ticket": "123"}),
        }
        with self.assertRaisesRegex(ValueError, "valid ISO datetime"):
            self.worker._persist(record)
        self.assertFalse(EAEvent.objects.filter(event_id="fill-invalid-001").exists())

    def test_market_bar_rejects_timezone_naive_timestamp(self):
        record = {
            "kind": "market_bar",
            "source": "ea-test-1",
            "event_id": "bar-naive-001",
            "occurred_at": "2026-10-22T10:00:00+00:00",
            "payload": json.dumps({
                "symbol": "XAUUSD_o",
                "timeframe": "m1",
                "timestamp": "2026-10-22T10:00:00",
                "open": "100",
                "high": "102",
                "low": "99",
                "close": "101",
                "volume": "3",
                "closed": True,
            }),
        }
        with self.assertRaisesRegex(ValueError, "market bar timestamp must include a timezone"):
            self.worker._persist(record)
        self.assertEqual(MarketBar.objects.count(), 0)

    def test_market_bar_persistence_is_idempotent_and_normalized(self):
        record = {"kind": "market_bar", "source": "ea-test-1", "event_id": "bar-001",
                  "occurred_at": "2026-10-22T10:00:00+00:00",
                  "payload": json.dumps({"symbol": "XAUUSD_o", "timeframe": "m1",
                      "timestamp": "2026-10-22T10:00:00+00:00", "open": "100", "high": "102",
                      "low": "99", "close": "101", "volume": "3", "closed": True})}
        self.worker._persist(record)
        self.worker._persist(record)
        self.assertEqual(MarketBar.objects.count(), 1)
        bar = MarketBar.objects.get()
        self.assertEqual(bar.timeframe, "M1")
        self.assertEqual(str(bar.close), "101.0000000000")

class ServerDailyBaselineTelemetryTests(TestCase):
    url = "/api/ea/v1/telemetry"
    headers = {"HTTP_AUTHORIZATION": "Bearer test-token"}

    def _payload(self, broker_day):
        return {
            "protocol_version": "ramon-ea-command-v1",
            "instance_id": "baseline-rollover-ea",
            "account_login": "98765432",
            "server_name": "LiteFinance-MT5-Demo",
            "broker_day": broker_day.isoformat(),
            "broker_time": int(time.time()),
            "symbol": "XAUUSD_o",
            "timeframe": "M15",
            "quote": {"bid": 2320.30, "ask": 2320.35, "point": 0.01},
            "account": {"balance": 10100, "equity": 10100, "margin": 0, "free_margin": 10100, "currency": "USD"},
            "positions": [],
            "bars": [],
        }

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_m15_timestamp_jitter_is_canonicalized_and_deduplicated(self):
        payload = self._payload(date.today())
        payload["instance_id"] = "m15-canonical-ea"
        payload["bars"] = [
            {
                "timestamp": "2026-11-02T03:30:08Z",
                "open": "2320.00", "high": "2321.00", "low": "2319.00",
                "close": "2320.35", "volume": "123", "closed": True,
            },
            {
                "timestamp": "2026-11-02T03:30:54Z",
                "open": "2320.00", "high": "2321.00", "low": "2319.00",
                "close": "2320.35", "volume": "123", "closed": True,
            },
        ]
        first = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["stored_bars"], 1)

        payload["bars"] = [dict(payload["bars"][0], timestamp="2026-11-02T03:31:39Z")]
        second = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()["stored_bars"], 1)

        bars = MarketBar.objects.filter(
            source="m15-canonical-ea", symbol="XAUUSD_o", timeframe="M15",
        )
        self.assertEqual(bars.count(), 1)
        self.assertEqual(bars.get().timestamp.isoformat(), "2026-11-02T03:30:00+00:00")

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_m15_timestamp_outside_tolerance_is_rejected(self):
        payload = self._payload(date.today())
        payload["instance_id"] = "m15-out-of-tolerance-ea"
        payload["bars"] = [{
            "timestamp": "2026-11-02T03:33:00Z",
            "open": "2320.00", "high": "2321.00", "low": "2319.00",
            "close": "2320.35", "volume": "123", "closed": True,
        }]
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("within 120 seconds", response.json()["error"])
        self.assertFalse(MarketBar.objects.filter(source="m15-out-of-tolerance-ea").exists())

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_conflicting_bars_in_one_m15_bucket_are_rejected(self):
        payload = self._payload(date.today())
        payload["instance_id"] = "m15-conflicting-ea"
        base = {
            "open": "2320.00", "high": "2321.00", "low": "2319.00",
            "close": "2320.35", "volume": "123", "closed": True,
        }
        payload["bars"] = [
            dict(base, timestamp="2026-11-02T03:30:08Z"),
            dict(base, timestamp="2026-11-02T03:30:54Z", close="2320.45"),
        ]
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("conflicts with another candle", response.json()["error"])
        self.assertFalse(MarketBar.objects.filter(source="m15-conflicting-ea").exists())

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_first_telemetry_bootstraps_baseline_from_complete_broker_history(self):
        payload = self._payload(date.today())
        payload.update({
            "broker_day_history_complete": True,
            "broker_day_opening_balance": "10000.00",
            "broker_day_deal_count": 1,
        })
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        baseline = AccountDailyBaseline.objects.get(
            account_login="98765432",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
        )
        self.assertEqual(str(baseline.opening_balance), "10000.00")
        self.assertEqual(baseline.capture_method, "BROKER_HISTORY_DERIVED")
        self.assertEqual(baseline.source_instance_id, "baseline-rollover-ea")

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_incomplete_broker_history_does_not_bootstrap_baseline(self):
        payload = self._payload(date.today())
        payload.update({
            "broker_day_history_complete": False,
            "broker_day_opening_balance": "10000.00",
            "broker_day_deal_count": 1,
        })
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(AccountDailyBaseline.objects.filter(
            account_login="98765432",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
        ).exists())

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_invalid_complete_broker_history_is_rejected(self):
        payload = self._payload(date.today())
        payload.update({
            "broker_day_history_complete": True,
            "broker_day_opening_balance": "0",
            "broker_day_deal_count": 0,
        })
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("broker_day_opening_balance must be positive", response.json()["error"])
        self.assertFalse(AccountDailyBaseline.objects.filter(
            account_login="98765432",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
        ).exists())

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_broker_history_does_not_overwrite_existing_baseline(self):
        baseline = AccountDailyBaseline.objects.create(
            account_login="98765432",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
            opening_balance="9900.00",
            source_instance_id="manual-verified",
            capture_method="MANUAL_BROKER_VERIFIED",
        )
        payload = self._payload(date.today())
        payload.update({
            "broker_day_history_complete": True,
            "broker_day_opening_balance": "10000.00",
            "broker_day_deal_count": 1,
        })
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        baseline.refresh_from_db()
        self.assertEqual(str(baseline.opening_balance), "9900.00")
        self.assertEqual(baseline.capture_method, "MANUAL_BROKER_VERIFIED")

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_continuous_rollover_records_previous_balance_as_immutable_baseline(self):
        yesterday = date.today() - timedelta(days=1)
        EAHeartbeat.objects.create(
            instance_id="baseline-rollover-ea",
            account_login="98765432",
            server_name="LiteFinance-MT5-Demo",
            symbol="XAUUSD_o",
            status="TELEMETRY_OK",
            terminal_connected=True,
            payload={
                "server_name": "LiteFinance-MT5-Demo",
                "broker_day": yesterday.isoformat(),
                "account": {"balance": "10000.00", "equity": "10000.00"},
                "positions": [],
            },
        )
        EAHeartbeat.objects.filter(instance_id="baseline-rollover-ea").update(
            last_seen=timezone.now() - timedelta(seconds=10)
        )
        response = self.client.post(
            self.url,
            data=json.dumps(self._payload(date.today())),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        baseline = AccountDailyBaseline.objects.get(
            account_login="98765432",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
        )
        self.assertEqual(str(baseline.opening_balance), "10000.00")
        self.assertEqual(baseline.source_instance_id, "baseline-rollover-ea")

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_missed_rollover_does_not_invent_a_baseline(self):
        yesterday = date.today() - timedelta(days=1)
        EAHeartbeat.objects.create(
            instance_id="baseline-rollover-ea",
            account_login="98765432",
            server_name="LiteFinance-MT5-Demo",
            symbol="XAUUSD_o",
            status="TELEMETRY_OK",
            terminal_connected=True,
            payload={
                "server_name": "LiteFinance-MT5-Demo",
                "broker_day": yesterday.isoformat(),
                "account": {"balance": "10000.00", "equity": "10000.00"},
                "positions": [],
            },
        )
        EAHeartbeat.objects.filter(instance_id="baseline-rollover-ea").update(
            last_seen=timezone.now() - timedelta(minutes=5)
        )
        response = self.client.post(
            self.url,
            data=json.dumps(self._payload(date.today())),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(AccountDailyBaseline.objects.filter(
            account_login="98765432",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
        ).exists())


class DjangoDecisionApiTests(TestCase):
    url="/api/ea/v1/decision"
    headers={"HTTP_AUTHORIZATION":"Bearer test-token"}

    def setUp(self):
        from .models import EAHeartbeat
        EAHeartbeat.objects.create(
            instance_id="ea-decision-test",
            account_login="12345678",
            server_name="LiteFinance-MT5-Demo",
            symbol="XAUUSD_o",
            status="TELEMETRY_OK",
            terminal_connected=True,
            trade_allowed=False,
            payload={
                "server_name": "LiteFinance-MT5-Demo",
                "account_trade_mode": 0,
                "broker_day": date.today().isoformat(),
                "account": {"balance": "10000", "equity": "10000",
                            "margin": "0", "free_margin": "10000",
                            "currency": "USD"},
                "positions": [],
                "symbol_info": {"tick_size": "0.01", "tick_value": "1.0", "volume_min": "0.01", "volume_max": "100", "volume_step": "0.01", "stops_level_points": "0"},
                "quote": {"bid": "2320.30", "ask": "2320.35", "point": "0.01"},
                "quote_time": timezone.now().isoformat(),
            },
        )

    def payload(self):
        bars=[]
        now=int(time.time())
        for i in range(256):
            c=2300+i*.08
            bars.append({"time":now-(256-i)*900,"open":c-.03,"high":c+.12,"low":c-.12,"close":c})
        return {"instance_id":"ea-decision-test","account_login":"12345678",
          "server_name":"LiteFinance-MT5-Demo",
          "symbol":"XAUUSD_o","timeframe":"M15","bid":2320.30,"ask":2320.35,"point":.01,"bars":bars,
          "account_balance":10000}
    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_complete_contract_and_audit(self):
        payload = self.payload()
        r=self.client.post(self.url,data=json.dumps(payload),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content); b=r.json()
        self.assertIn(b["decision"],("BUY","SELL","WAIT")); self.assertEqual(len(b["sample_key"]),16)
        self.assertEqual(b["signal_bar_time"],payload["bars"][-1]["time"])
        self.assertFalse(b["trading_authorization"]); self.assertFalse(b["execution_enabled"])
        self.assertTrue(b["risk_inputs_complete"])
        self.assertEqual(b["missing_risk_inputs"], "")
        self.assertEqual(
            set(b["risk_inputs_received"].split(",")),
            {"stop_level_points", "take_profit_points", "open_risk_percent"},
        )
        for k in ("base_decision","base_reason","ensemble_ready","regime_probability","news_source","news_source_ready",
          "news_source_age_seconds","signal_bid","signal_ask","forecast_low","forecast_median","forecast_high","atr",
          "intrabar_confirmed","intrabar_direction","ai_trend_confirmed","ai_trend_direction","spread_points",
          "stop_distance","target_distance","target_tp1","target_tp2","target_tp3","legacy_target_price"):
            self.assertIn(k,b)
        from .models import StrategyDecisionRecord
        record = StrategyDecisionRecord.objects.get(decision_id=b["sample_key"])
        self.assertEqual(record.features["opportunity_input_path"], "completed_ohlc_structural")
        opportunity = record.features["stages"]["opportunities"]
        self.assertTrue(any(check["name"] == "data_valid" for check in opportunity["checks"]))
        self.assertTrue(any(
            check["name"] == "bar_complete" and check["passed"] is True
            for check in opportunity["checks"]
        ))
        self.assertEqual(record.model_version, "django-signalflow-structural-v2")
        verdict = RiskVerdictRecord.objects.filter(decision_id=b["sample_key"]).latest("created_at")
        self.assertEqual(verdict.approved, b["risk_approved_advisory_only"])
        self.assertFalse(verdict.details["execution_authorized"])
        self.assertFalse(verdict.details["command_created"])
        if not verdict.approved:
            self.assertEqual(verdict.approved_volume, 0)
    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_DJANGO_DECISION_ENABLED=True, RAMON_EA_COMMAND_CREATION_ENABLED=False, RAMON_EA_COMMAND_EXECUTION_ENABLED=False)
    @patch("ea_bridge.decision.SignalFlowPipeline.run")
    def test_approved_risk_verdict_remains_non_executable(self, run_pipeline):
        AccountDailyBaseline.objects.create(
            account_login="12345678",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
            opening_balance="10000.00",
            source_instance_id="ea-decision-test",
        )
        run_pipeline.return_value = {
            "approved": True,
            "action": "BUY",
            "reason": "SIGNALFLOW_APPROVED",
            "stages": {
                "opportunities": {"approved": True, "score": 0.9, "checks": []},
                "entry_timing": {"approved": True, "checks": []},
                "risk_management": {
                    "approved": True, "reason": "RISK_APPROVED", "volume": 0.1,
                    "stop_price": 2319.0, "stop_points": 130.0, "checks": [],
                },
            },
        }
        payload = self.payload()
        response = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **self.headers
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["decision"], "BUY")
        self.assertTrue(body["risk_approved_advisory_only"])
        verdict = RiskVerdictRecord.objects.filter(decision_id=body["sample_key"]).latest("created_at")
        self.assertTrue(verdict.approved)
        self.assertEqual(verdict.reason_code, "RISK_APPROVED_ADVISORY_ONLY")
        self.assertEqual(verdict.approved_volume, Decimal("0.1"))
        self.assertEqual(verdict.stop_loss, Decimal("2319.0"))
        self.assertGreater(verdict.take_profit, Decimal("2320.35"))
        self.assertFalse(verdict.details["execution_authorized"])
        self.assertFalse(verdict.details["command_created"])

    @override_settings(
        EA_BEARER_TOKEN="test-token",
        RAMON_DJANGO_DECISION_ENABLED=True,
        RAMON_EA_COMMAND_CREATION_ENABLED=True,
        RAMON_EA_COMMAND_EXECUTION_ENABLED=True,
    )
    @patch("ea_bridge.decision.SignalFlowPipeline.run")
    def test_command_is_created_once_only_after_approved_risk_and_fresh_demo_permissions(self, run_pipeline):
        heartbeat = EAHeartbeat.objects.get(instance_id="ea-decision-test")
        heartbeat.trade_allowed = True
        heartbeat.payload.update({
            "broker_day_history_complete": True,
            "broker_day_opening_balance": "10000.00",
        })
        heartbeat.save(update_fields=["trade_allowed", "payload"])
        run_pipeline.return_value = {
            "approved": True,
            "action": "BUY",
            "reason": "SIGNALFLOW_APPROVED",
            "stages": {
                "opportunities": {"approved": True, "score": 0.9, "checks": []},
                "entry_timing": {"approved": True, "checks": []},
                "risk_management": {
                    "approved": True, "reason": "RISK_APPROVED", "volume": 0.1,
                    "stop_price": 2319.0, "stop_points": 130.0, "checks": [],
                },
            },
        }
        payload = self.payload()
        first = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **self.headers
        )
        self.assertEqual(first.status_code, 200, first.content)
        body = first.json()
        self.assertTrue(body["command_created"], body)
        self.assertTrue(body["command_available"], body)
        self.assertEqual(body["command_reason"], "COMMAND_CREATED")
        self.assertEqual(body["daily_loss_source"], "UNAVAILABLE_FAIL_CLOSED")
        command = EACommand.objects.get(command_id=body["command_id"])
        self.assertEqual(command.action, EACommand.Action.OPEN)
        self.assertEqual(command.instance_id, "ea-decision-test")
        self.assertEqual(command.account_login, "12345678")
        self.assertEqual(command.symbol, "XAUUSD_o")
        self.assertEqual(command.payload["server_name"], "LiteFinance-MT5-Demo")
        self.assertEqual(command.payload["side"], "BUY")
        self.assertEqual(command.payload["volume"], 0.1)
        self.assertTrue(command.execution_authorized)
        verdict = RiskVerdictRecord.objects.filter(decision_id=body["sample_key"]).latest("created_at")
        self.assertFalse(verdict.details["execution_authorized"])
        self.assertTrue(verdict.details["command_created"])

        second = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **self.headers
        )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertFalse(second.json()["command_created"])
        self.assertTrue(second.json()["command_available"])
        self.assertEqual(second.json()["command_id"], body["command_id"])
        self.assertEqual(EACommand.objects.count(), 1)

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_same_bar_record_refreshes_when_quote_changes(self):
        payload = self.payload()
        first = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **self.headers
        )
        self.assertEqual(first.status_code, 200, first.content)
        first_key = first.json()["sample_key"]

        payload["bid"] += 0.02
        payload["ask"] += 0.02
        second = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **self.headers
        )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()["sample_key"], first_key)

        from .models import StrategyDecisionRecord
        self.assertEqual(StrategyDecisionRecord.objects.filter(decision_id=first_key).count(), 1)
        record = StrategyDecisionRecord.objects.get(decision_id=first_key)
        self.assertEqual(record.features["market_snapshot"]["bid"], payload["bid"])
        self.assertEqual(record.features["market_snapshot"]["ask"], payload["ask"])
        self.assertEqual(record.model_version, "django-signalflow-structural-v2")

    @override_settings(EA_BEARER_TOKEN="wrong-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_rejects_wrong_token(self):
        r=self.client.post(self.url,data=json.dumps(self.payload()),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,401)
    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=False)
    def test_can_be_disabled(self):
        r=self.client.post(self.url,data=json.dumps(self.payload()),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,503)
    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_rejects_unordered_bar_timestamps(self):
        p=self.payload()
        p["bars"][10]["time"]=p["bars"][9]["time"]
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,400)
        self.assertIn("strictly increasing",r.json()["error"])

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_accepts_session_gap_with_ordered_fresh_bars(self):
        p = self.payload()
        # Simulate a multi-hour market closure earlier in the history.
        for bar in p["bars"][:21]:
            bar["time"] -= 4 * 3600

        r = self.client.post(
            self.url,
            data=json.dumps(p),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(r.status_code, 200, r.content)

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_rejects_future_latest_bar(self):
        p = self.payload()
        p["bars"][-1]["time"] = int(time.time()) + 120
        r = self.client.post(
            self.url,
            data=json.dumps(p),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("future", r.json()["error"])

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_rejects_stale_latest_bar(self):
        p=self.payload()
        old=int(time.time())-7200
        for i,bar in enumerate(p["bars"]):
            bar["time"]=old-(len(p["bars"])-1-i)*900
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,400)
        self.assertIn("stale",r.json()["error"])

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_missing_server_symbol_metadata_never_produces_trade(self):
        p=self.payload()
        heartbeat = EAHeartbeat.objects.get(instance_id="ea-decision-test")
        heartbeat.payload.pop("symbol_info", None)
        heartbeat.save(update_fields=["payload"])
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        b=r.json()
        self.assertEqual(b["decision"],"WAIT")
        self.assertEqual(b["reason"],"SERVER_RISK_INPUTS_UNAVAILABLE")
        self.assertFalse(b["risk_inputs_complete"])
        self.assertIn("server_symbol_metadata", b["missing_risk_inputs"])
        self.assertFalse(b["risk_approved_advisory_only"])
        self.assertFalse(b["trading_authorization"])
        self.assertFalse(b["execution_enabled"])

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_missing_daily_baseline_does_not_block_decision(self):
        heartbeat = EAHeartbeat.objects.get(instance_id="ea-decision-test")
        heartbeat.payload["account_trade_mode"] = 1
        heartbeat.payload.pop("broker_day", None)
        heartbeat.payload.pop("broker_day_history_complete", None)
        heartbeat.save(update_fields=["payload"])
        response = self.client.post(
            self.url, data=json.dumps(self.payload()),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("DAILY_LOSS", response.json()["reason"])
        self.assertFalse(response.json()["daily_baseline_available"])

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_large_daily_drawdown_does_not_create_daily_loss_rejection(self):
        AccountDailyBaseline.objects.create(
            account_login="12345678",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
            opening_balance="10000.00",
            source_instance_id="ea-decision-test",
        )
        heartbeat = EAHeartbeat.objects.get(instance_id="ea-decision-test")
        heartbeat.payload["account"]["equity"] = "7000"
        heartbeat.save(update_fields=["payload"])
        response = self.client.post(
            self.url, data=json.dumps(self.payload()),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("DAILY_LOSS", response.json()["reason"])

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_rejects_account_login_mismatch(self):
        p=self.payload()
        p["account_login"]="99999999"
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(r.json()["decision"],"WAIT")
        self.assertEqual(r.json()["reason"],"ACCOUNT_LOGIN_MISMATCH")

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_client_balance_value_is_ignored_in_favor_of_heartbeat(self):
        p=self.payload()
        p["account_balance"]=1
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        self.assertNotEqual(r.json()["reason"],"ACCOUNT_BALANCE_MISMATCH")
        self.assertTrue(r.json()["risk_inputs_complete"])

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_rejects_missing_instance_identity(self):
        p=self.payload()
        p.pop("instance_id")
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(r.json()["decision"],"WAIT")
        self.assertEqual(r.json()["reason"],"ACCOUNT_IDENTITY_MISSING")

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_rejects_insufficient_bars(self):
        p=self.payload(); p["bars"]=p["bars"][:10]
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,400)

class CommandPollingTests(TestCase):
    url = "/api/ea/v1/commands/next"
    headers = {"HTTP_AUTHORIZATION": "Bearer test-token"}

    @override_settings(
        EA_BEARER_TOKEN="test-token",
        RAMON_EA_COMMAND_EXECUTION_ENABLED=True,
        RAMON_EA_HEARTBEAT_MAX_AGE_SECONDS=90,
    )
    def test_next_command_delivers_only_authorized_pending_command_to_matching_account(self):
        EAHeartbeat.objects.create(
            instance_id="command-poll-ea",
            account_login="12345678",
            server_name="LiteFinance-MT5-Demo",
            symbol="XAUUSD_o",
            status="TELEMETRY_OK",
            terminal_connected=True,
            trade_allowed=True,
            payload={"server_name": "LiteFinance-MT5-Demo", "account_trade_mode": 0},
        )
        command = EACommand.objects.create(
            command_id="ramon-open-test-001",
            instance_id="command-poll-ea",
            account_login="12345678",
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={
                "server_name": "LiteFinance-MT5-Demo",
                "side": "BUY",
                "volume": 0.01,
                "stop_loss": 2300.0,
                "take_profit": 2310.0,
            },
            decision_id="decision-test-001",
            risk_verdict_id="risk-test-001",
            execution_authorized=True,
            expires_at=timezone.now() + timedelta(seconds=60),
        )
        response = self.client.post(
            self.url,
            data=json.dumps({
                "instance_id": "command-poll-ea",
                "account_login": "12345678",
            }),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["trading_authorization"])
        self.assertTrue(body["commands_enabled"])
        self.assertTrue(body["command"]["execution_authorized"])
        self.assertEqual(body["command"]["command_id"], command.command_id)
        command.refresh_from_db()
        self.assertEqual(command.state, EACommand.State.CLAIMED)

    @override_settings(
        EA_BEARER_TOKEN="test-token",
        RAMON_EA_COMMAND_EXECUTION_ENABLED=True,
    )
    def test_next_command_never_delivers_unauthorized_or_other_account_command(self):
        EACommand.objects.create(
            command_id="ramon-open-unauthorized",
            instance_id="command-poll-ea",
            account_login="12345678",
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={"side": "BUY"},
            execution_authorized=False,
            expires_at=timezone.now() + timedelta(seconds=60),
        )
        EACommand.objects.create(
            command_id="ramon-open-other-account",
            instance_id="command-poll-ea",
            account_login="87654321",
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={"side": "BUY"},
            execution_authorized=True,
            expires_at=timezone.now() + timedelta(seconds=60),
        )
        response = self.client.post(
            self.url,
            data=json.dumps({
                "instance_id": "command-poll-ea",
                "account_login": "12345678",
            }),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["command"])
        self.assertFalse(response.json()["trading_authorization"])

    @override_settings(
        EA_BEARER_TOKEN="test-token",
        RAMON_EA_COMMAND_EXECUTION_ENABLED=True,
        RAMON_EA_HEARTBEAT_MAX_AGE_SECONDS=90,
    )
    def test_next_command_blocks_stale_heartbeat(self):
        heartbeat = EAHeartbeat.objects.create(
            instance_id="command-poll-ea",
            account_login="12345678",
            server_name="LiteFinance-MT5-Demo",
            symbol="XAUUSD_o",
            status="TELEMETRY_OK",
            terminal_connected=True,
            trade_allowed=True,
            payload={"server_name": "LiteFinance-MT5-Demo", "account_trade_mode": 0},
        )
        EAHeartbeat.objects.filter(pk=heartbeat.pk).update(
            last_seen=timezone.now() - timedelta(seconds=120)
        )
        EACommand.objects.create(
            command_id="ramon-open-stale-heartbeat",
            instance_id="command-poll-ea",
            account_login="12345678",
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={"side": "BUY", "volume": 0.01},
            execution_authorized=True,
            expires_at=timezone.now() + timedelta(seconds=60),
        )
        response = self.client.post(
            self.url,
            data=json.dumps({"instance_id": "command-poll-ea", "account_login": "12345678"}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["command"])
        self.assertEqual(response.json()["authorization_reason"], "HEARTBEAT_STALE")

    @override_settings(
        EA_BEARER_TOKEN="test-token",
        RAMON_EA_COMMAND_EXECUTION_ENABLED=True,
    )
    def test_next_command_blocks_non_demo_heartbeat(self):
        EAHeartbeat.objects.create(
            instance_id="command-poll-ea",
            account_login="12345678",
            server_name="LiteFinance-MT5-Live",
            symbol="XAUUSD_o",
            status="TELEMETRY_OK",
            terminal_connected=True,
            trade_allowed=True,
            payload={"server_name": "LiteFinance-MT5-Live", "account_trade_mode": 1},
        )
        EACommand.objects.create(
            command_id="ramon-open-live-account",
            instance_id="command-poll-ea",
            account_login="12345678",
            symbol="XAUUSD_o",
            action=EACommand.Action.OPEN,
            payload={"side": "BUY", "volume": 0.01},
            execution_authorized=True,
            expires_at=timezone.now() + timedelta(seconds=60),
        )
        response = self.client.post(
            self.url,
            data=json.dumps({"instance_id": "command-poll-ea", "account_login": "12345678"}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["command"])
        self.assertEqual(response.json()["authorization_reason"], "DEMO_ACCOUNT_REQUIRED")


class DashboardDataTests(TestCase):
    url = "/api/ea/v1/dashboard-data"

    @override_settings(
        RAMON_EA_COMMAND_CREATION_ENABLED=True,
        RAMON_EA_COMMAND_EXECUTION_ENABLED=False,
        RAMON_EA_HEARTBEAT_MAX_AGE_SECONDS=90,
    )
    def test_dashboard_exposes_live_execution_checks_without_account_identifiers(self):
        EAHeartbeat.objects.create(
            instance_id="dashboard-live-ea",
            account_login="sensitive-account-id",
            server_name="private-broker-server",
            symbol="XAUUSD_o",
            status="TELEMETRY_OK",
            terminal_connected=True,
            trade_allowed=False,
            payload={"account_trade_mode": 0},
        )
        StrategyDecisionRecord.objects.create(
            decision_id="dashboard-decision",
            symbol="XAUUSD_o",
            action="WAIT",
            confidence=0.0,
            rationale_code="MARKET_UNCERTAIN",
            model_version="test-model",
            features={
                "stages": {
                    "opportunities": {
                        "approved": False,
                        "status": "DEFERRED",
                        "checks": [
                            {"name": "regime", "passed": False, "detail": "Live regime result",
                             "metrics": {"value": 17.5, "threshold": 22.0}},
                        ],
                    },
                },
            },
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["decisions"][0]["features"]["stages"]["opportunities"]["checks"][0]["metrics"]["value"], 17.5)

        execution = body["execution"]
        checks = {item["name"]: item for item in execution["checks"]}
        self.assertFalse(execution["trading_authorization"])
        self.assertTrue(execution["command_creation_enabled"])
        self.assertFalse(execution["command_execution_enabled"])
        self.assertTrue(checks["ea_heartbeat"]["passed"])
        self.assertTrue(checks["telemetry_status"]["passed"])
        self.assertTrue(checks["terminal_connected"]["passed"])
        self.assertFalse(checks["terminal_trade_allowed"]["passed"])
        self.assertTrue(checks["demo_account"]["passed"])
        self.assertTrue(checks["symbol_match"]["passed"])
        self.assertNotIn("sensitive-account-id", json.dumps(execution))
        self.assertNotIn("private-broker-server", json.dumps(execution))

    @override_settings(
        RAMON_EA_COMMAND_CREATION_ENABLED=True,
        RAMON_EA_COMMAND_EXECUTION_ENABLED=True,
        RAMON_EA_HEARTBEAT_MAX_AGE_SECONDS=90,
    )
    def test_dashboard_reports_command_authorization_ready_for_fresh_demo_ea(self):
        EAHeartbeat.objects.create(
            instance_id="dashboard-authorized-ea",
            account_login="private-account-id",
            server_name="private-demo-server",
            symbol="XAUUSD_o",
            status="TELEMETRY_OK",
            terminal_connected=True,
            trade_allowed=True,
            payload={"account_trade_mode": 0},
        )
        StrategyDecisionRecord.objects.create(
            decision_id="dashboard-authorized-decision",
            symbol="XAUUSD_o",
            action="WAIT",
            confidence=0.0,
            rationale_code="NO_SETUP_YET",
            model_version="test-model",
            features={},
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        execution = response.json()["execution"]
        checks = {item["name"]: item for item in execution["checks"]}

        self.assertTrue(execution["trading_authorization"])
        self.assertTrue(checks["django_authorization"]["passed"])
        self.assertIn("individually risk-approved", execution["authorization_detail"])
        self.assertTrue(checks["ea_heartbeat"]["passed"])
        self.assertTrue(checks["terminal_connected"]["passed"])
        self.assertTrue(checks["terminal_trade_allowed"]["passed"])
        self.assertTrue(checks["demo_account"]["passed"])
        self.assertTrue(checks["symbol_match"]["passed"])
        # Dashboard readiness is not itself an order authorization.
        self.assertFalse(EACommand.objects.exists())
        self.assertNotIn("private-account-id", json.dumps(execution))
        self.assertNotIn("private-demo-server", json.dumps(execution))

    @override_settings(RAMON_EA_HEARTBEAT_MAX_AGE_SECONDS=90)
    def test_dashboard_marks_missing_and_stale_heartbeats_as_waiting_or_blocked(self):
        missing = self.client.get(self.url).json()["execution"]
        missing_checks = {item["name"]: item for item in missing["checks"]}
        self.assertIsNone(missing_checks["ea_heartbeat"]["passed"])
        self.assertEqual(missing_checks["ea_heartbeat"]["status"], "WAITING")

        heartbeat = EAHeartbeat.objects.create(
            instance_id="dashboard-stale-ea",
            symbol="XAUUSD_o",
            terminal_connected=True,
            trade_allowed=True,
            payload={"account_trade_mode": 0},
        )
        EAHeartbeat.objects.filter(pk=heartbeat.pk).update(
            last_seen=timezone.now() - timedelta(seconds=120)
        )
        stale = self.client.get(self.url).json()["execution"]
        stale_checks = {item["name"]: item for item in stale["checks"]}
        self.assertFalse(stale_checks["ea_heartbeat"]["passed"])
        self.assertEqual(stale_checks["ea_heartbeat"]["status"], "BLOCKED")

class DashboardTemplateTests(TestCase):
    def test_decision_flow_template_uses_live_payload_instead_of_static_catalogue(self):
        response = self.client.get("/api/ea/v1/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "decisionStageEntries")
        self.assertContains(response, "flowMetricsMarkup")
        self.assertContains(response, "renderDecisionVariableGuide(latest, data.execution || {})")
        self.assertNotContains(response, "DECISION_VARIABLE_GROUPS")
        self.assertNotContains(response, "orderedEntryFlowChecks")
        self.assertNotContains(response, "orderedRiskFlowChecks")

    def test_dashboard_exposes_both_opportunity_detection_paths(self):
        response = self.client.get("/api/ea/v1/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "opportunityDetectorMarkup")
        self.assertContains(response, "Trend Pullback")
        self.assertContains(response, "Breakout / Retest")
        self.assertContains(response, "NOT EVALUATED")



class MultiTimeframeMarketDataTests(TestCase):
    url = "/api/ea/v1/telemetry"
    status_url = "/api/ea/v1/market-data/status"
    headers = {"HTTP_AUTHORIZATION": "Bearer test-token"}

    def _payload(self, bar_sets):
        return {
            "protocol_version": "ramon-ea-command-v1",
            "instance_id": "multi-timeframe-ea",
            "account_login": "98765432",
            "server_name": "LiteFinance-MT5-Demo",
            "broker_day": date.today().isoformat(),
            "symbol": "XAUUSD_o",
            "timeframe": "M15",
            "quote": {"bid": 2320.30, "ask": 2320.35, "point": 0.01},
            "account": {"balance": 10100, "equity": 10100, "margin": 0, "free_margin": 10100, "currency": "USD"},
            "positions": [],
            "bars": [],
            "bar_sets": bar_sets,
        }

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_all_four_timeframes_are_persisted_and_retries_are_idempotent(self):
        candle = {
            "open": "2320.00", "high": "2321.00", "low": "2319.00",
            "close": "2320.35", "volume": "123", "closed": True,
        }
        bar_sets = [
            {"timeframe": "M1", "bars": [dict(candle, timestamp="2026-11-02T03:30:00Z")]},
            {"timeframe": "M5", "bars": [dict(candle, timestamp="2026-11-02T03:30:00Z")]},
            {"timeframe": "M15", "bars": [dict(candle, timestamp="2026-11-02T03:30:00Z")]},
            {"timeframe": "H1", "bars": [dict(candle, timestamp="2026-11-02T03:00:00Z")]},
        ]
        payload = self._payload(bar_sets)
        first = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["stored_bars"], 4)
        self.assertEqual(first.json()["inserted_bars"], 4)
        self.assertEqual(
            set(MarketBar.objects.filter(source="multi-timeframe-ea").values_list("timeframe", flat=True)),
            {"M1", "M5", "M15", "H1"},
        )

        second = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()["inserted_bars"], 0)
        self.assertEqual(MarketBar.objects.filter(source="multi-timeframe-ea").count(), 4)

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_late_arriving_candle_is_reported_without_replacing_existing_data(self):
        candle = {
            "open": "2320.00", "high": "2321.00", "low": "2319.00",
            "close": "2320.35", "volume": "123", "closed": True,
        }
        first_payload = self._payload([{
            "timeframe": "M1",
            "bars": [
                dict(candle, timestamp="2026-11-02T03:30:00Z"),
                dict(candle, timestamp="2026-11-02T03:31:00Z"),
            ],
        }])
        first = self.client.post(
            self.url, data=json.dumps(first_payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(first.status_code, 200, first.content)

        late_payload = self._payload([{
            "timeframe": "M1",
            "bars": [dict(candle, timestamp="2026-11-02T03:29:00Z", close="2320.40")],
        }])
        late = self.client.post(
            self.url, data=json.dumps(late_payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(late.status_code, 200, late.content)
        self.assertEqual(late.json()["timeframes"][0]["late_arrivals"], 1)
        self.assertEqual(MarketBar.objects.filter(source="multi-timeframe-ea", timeframe="M1").count(), 3)

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_status_reports_last_successful_candle_and_potential_intraday_gaps(self):
        candle = {
            "open": "2320.00", "high": "2321.00", "low": "2319.00",
            "close": "2320.35", "volume": "123", "closed": True,
        }
        payload = self._payload([{
            "timeframe": "M1",
            "bars": [
                dict(candle, timestamp="2026-11-02T03:30:00Z"),
                dict(candle, timestamp="2026-11-02T03:32:00Z"),
            ],
        }])
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)

        status = self.client.get(
            self.status_url + "?symbol=XAUUSD_o&source=multi-timeframe-ea",
            **self.headers,
        )
        self.assertEqual(status.status_code, 200, status.content)
        reports = {item["timeframe"]: item for item in status.json()["timeframes"]}
        self.assertEqual(reports["M1"]["candle_count"], 2)
        self.assertEqual(reports["M1"]["last_successful_candle_utc"], "2026-11-02T03:32:00+00:00")
        self.assertEqual(len(reports["M1"]["potential_intraday_gaps"]), 1)
        self.assertEqual(reports["M5"]["status"], "NO_DATA")



class MarketBarRetentionCommandTests(TestCase):
    def test_retention_is_dry_run_by_default_and_requires_explicit_apply(self):
        from datetime import timedelta
        from django.core.management import call_command
        from django.utils import timezone

        old_stamp = timezone.now() - timedelta(days=800)
        MarketBar.objects.create(
            source="retention-test", symbol="XAUUSD_o", timeframe="M1",
            timestamp=old_stamp, open="2300", high="2301", low="2299",
            close="2300.5", volume="10",
        )
        MarketBar.objects.create(
            source="retention-test", symbol="XAUUSD_o", timeframe="M5",
            timestamp=old_stamp, open="2300", high="2301", low="2299",
            close="2300.5", volume="10",
        )

        call_command("prune_market_bars", source="retention-test", stdout=Mock())
        self.assertEqual(MarketBar.objects.filter(source="retention-test").count(), 2)

        call_command("prune_market_bars", source="retention-test", apply=True, stdout=Mock())
        self.assertFalse(MarketBar.objects.filter(source="retention-test", timeframe="M1").exists())
        self.assertTrue(MarketBar.objects.filter(source="retention-test", timeframe="M5").exists())


    def test_market_bar_rejects_incomplete_candle(self):
        record = {
            "kind": "market_bar",
            "source": "ea-test-1",
            "event_id": "bar-open-001",
            "occurred_at": "2026-10-22T10:00:00+00:00",
            "payload": json.dumps({
                "symbol": "XAUUSD_o", "timeframe": "m1",
                "timestamp": "2026-10-22T10:00:00+00:00",
                "open": "100", "high": "102", "low": "99",
                "close": "101", "volume": "3", "closed": False,
            }),
        }
        with self.assertRaisesRegex(ValueError, "completed candle"):
            Command._persist(record)
        self.assertEqual(MarketBar.objects.count(), 0)
