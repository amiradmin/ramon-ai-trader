import json
import time
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from .models import EAEvent, MarketBar
from ramon_pipeline.management.commands.consume_ramon_streams import Command


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
                      "low": "99", "close": "101", "volume": "3"})}
        self.worker._persist(record)
        self.worker._persist(record)
        self.assertEqual(MarketBar.objects.count(), 1)
        bar = MarketBar.objects.get()
        self.assertEqual(bar.timeframe, "M1")
        self.assertEqual(str(bar.close), "101.0000000000")

class DjangoDecisionApiTests(TestCase):
    url="/api/ea/v1/decision"
    headers={"HTTP_AUTHORIZATION":"Bearer test-token"}
    def payload(self):
        bars=[]
        now=int(time.time())
        for i in range(256):
            c=2300+i*.08
            bars.append({"time":now-(256-i)*900,"open":c-.03,"high":c+.12,"low":c-.12,"close":c})
        return {"symbol":"XAUUSD_o","timeframe":"M15","bid":2320.30,"ask":2320.35,"point":.01,"bars":bars,
          "account_balance":10000,"risk_percent":.06,"point_value_per_lot":1,"volume_step":.01,"min_volume":.01,"max_volume":100,
          "stop_level_points":0,"take_profit_points":1000,"daily_loss_percent":0,"open_risk_percent":0}
    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_complete_contract_and_audit(self):
        r=self.client.post(self.url,data=json.dumps(self.payload()),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content); b=r.json()
        self.assertIn(b["decision"],("BUY","SELL","WAIT")); self.assertEqual(len(b["sample_key"]),16)
        self.assertEqual(b["signal_bar_time"],self.payload()["bars"][-1]["time"])
        self.assertFalse(b["trading_authorization"]); self.assertFalse(b["execution_enabled"])
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
    def test_missing_risk_guards_never_produce_buy_or_sell(self):
        p=self.payload()
        p.pop("daily_loss_percent")
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        b=r.json()
        self.assertEqual(b["decision"],"WAIT")
        self.assertEqual(b["reason"],"RISK_INPUTS_INCOMPLETE")
        self.assertFalse(b["risk_approved_advisory_only"])
        self.assertFalse(b["trading_authorization"])
        self.assertFalse(b["execution_enabled"])

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_rejects_insufficient_bars(self):
        p=self.payload(); p["bars"]=p["bars"][:10]
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,400)
