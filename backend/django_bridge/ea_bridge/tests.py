import json
import time
from datetime import date, timedelta
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import AccountDailyBaseline, EAEvent, EAHeartbeat, MarketBar, StrategyDecisionRecord
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
                "broker_day": date.today().isoformat(),
                "account": {"balance": "10000", "equity": "10000",
                            "margin": "0", "free_margin": "10000",
                            "currency": "USD"},
                "positions": [],
                "quote": {"bid": "2320.30", "ask": "2320.35", "point": "0.01"},
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
          "account_balance":10000,"risk_percent":.06,"point_value_per_lot":1,"volume_step":.01,"min_volume":.01,"max_volume":100,
          "stop_level_points":0,"take_profit_points":1000,"daily_loss_percent":0,"open_risk_percent":0}
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
            {"stop_level_points", "take_profit_points", "daily_loss_percent", "open_risk_percent"},
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
    def test_missing_risk_guards_never_produce_buy_or_sell(self):
        p=self.payload()
        p.pop("daily_loss_percent")
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        b=r.json()
        self.assertEqual(b["decision"],"WAIT")
        self.assertEqual(b["reason"],"RISK_INPUTS_INCOMPLETE")
        self.assertFalse(b["risk_inputs_complete"])
        self.assertEqual(b["missing_risk_inputs"], "daily_loss_percent")
        self.assertEqual(
            set(b["risk_inputs_received"].split(",")),
            {"stop_level_points", "take_profit_points", "open_risk_percent"},
        )
        self.assertFalse(b["risk_approved_advisory_only"])
        self.assertFalse(b["trading_authorization"])
        self.assertFalse(b["execution_enabled"])

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_client_daily_loss_zero_does_not_authorize_without_server_baseline(self):
        r=self.client.post(self.url,data=json.dumps(self.payload()),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        b=r.json()
        self.assertEqual(b["decision"],"WAIT")
        self.assertEqual(b["reason"],"DAILY_LOSS_BASELINE_UNAVAILABLE")
        self.assertFalse(b["trading_authorization"])
        self.assertFalse(b["execution_enabled"])

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_server_baseline_equity_drawdown_overrides_client_zero(self):
        p = self.payload()
        AccountDailyBaseline.objects.create(
            account_login="12345678",
            server_name="LiteFinance-MT5-Demo",
            broker_day=date.today(),
            opening_balance="10000.00",
            source_instance_id="ea-decision-test",
        )
        heartbeat = EAHeartbeat.objects.get(instance_id="ea-decision-test")
        heartbeat.payload["account"]["equity"] = "9600"
        heartbeat.save(update_fields=["payload"])
        p["daily_loss_percent"] = 0
        r = self.client.post(self.url, data=json.dumps(p), content_type="application/json", **self.headers)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["decision"], "WAIT")
        self.assertTrue(body["daily_baseline_available"])
        self.assertEqual(body["daily_baseline_day"], date.today().isoformat())
        self.assertAlmostEqual(body["server_daily_loss_percent"], 4.0, places=4)
        self.assertEqual(body["daily_loss_source"], "SERVER_BASELINE_EQUITY_DRAWDOWN")
        audit = StrategyDecisionRecord.objects.get(decision_id=body["sample_key"])
        self.assertAlmostEqual(audit.features["daily_risk"]["loss_percent"], 4.0, places=4)
        self.assertEqual(audit.features["daily_risk"]["source"], "SERVER_BASELINE_EQUITY_DRAWDOWN")

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_rejects_account_login_mismatch(self):
        p=self.payload()
        p["account_login"]="99999999"
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(r.json()["decision"],"WAIT")
        self.assertEqual(r.json()["reason"],"ACCOUNT_LOGIN_MISMATCH")

    @override_settings(EA_BEARER_TOKEN="test-token",RAMON_DJANGO_DECISION_ENABLED=True)
    def test_rejects_balance_mismatch(self):
        p=self.payload()
        p["account_balance"]=9999
        r=self.client.post(self.url,data=json.dumps(p),content_type="application/json",**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(r.json()["decision"],"WAIT")
        self.assertEqual(r.json()["reason"],"ACCOUNT_BALANCE_MISMATCH")

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
