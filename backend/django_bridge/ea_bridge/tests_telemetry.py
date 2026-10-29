import json
from django.test import TestCase, override_settings
from .models import EAHeartbeat, MarketBar


class EATelemetryTests(TestCase):
    headers = {"HTTP_AUTHORIZATION": "Bearer test-token"}
    url = "/api/ea/v1/telemetry"

    def payload(self):
        return {
            "protocol_version": "ramon-ea-command-v1",
            "ea_version": "1.02",
            "account_trade_mode": 0,
            "instance_id": "ea-telemetry-test",
            "account_login": "12345",
            "symbol": "XAUUSD_o",
            "timeframe": "M15",
            "quote": {"bid": 2320.30, "ask": 2320.35, "point": 0.01},
            "quote_time": "2026-10-27T09:45:00Z",
            "account": {"currency": "USD", "balance": 10000, "equity": 10000},
            "positions": [],
            "bars": [{
                "timestamp": "2026-10-27T09:30:00Z",
                "open": 2320.0, "high": 2322.0, "low": 2319.0, "close": 2321.0,
                "volume": 12, "closed": True,
            }],
        }

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_stores_completed_bar_and_snapshot_without_authorizing_trading(self):
        response = self.client.post(self.url, data=json.dumps(self.payload()), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["telemetry_only"])
        self.assertFalse(response.json()["trading_authorization"])
        self.assertEqual(MarketBar.objects.filter(source="ea-telemetry-test").count(), 1)
        heartbeat = EAHeartbeat.objects.get(instance_id="ea-telemetry-test")
        self.assertEqual(heartbeat.ea_version, "1.02")
        self.assertEqual(heartbeat.payload["ea_version"], "1.02")
        self.assertEqual(heartbeat.payload["account_trade_mode"], 0)

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_rejects_unclosed_bar(self):
        payload = self.payload()
        payload["bars"][0]["closed"] = False
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MarketBar.objects.count(), 0)

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_rejects_timezone_naive_bar(self):
        payload = self.payload()
        payload["bars"][0]["timestamp"] = "2026-10-27T09:30:00"
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 400)
