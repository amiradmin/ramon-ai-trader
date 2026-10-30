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

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_rejects_m15_bar_shifted_by_one_second(self):
        payload = self.payload()
        payload["bars"][0]["timestamp"] = "2026-10-27T09:30:01Z"
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("15-minute boundary", response.json()["error"])
        self.assertEqual(MarketBar.objects.count(), 0)

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_rejects_m15_bar_off_quarter_hour_boundary(self):
        payload = self.payload()
        payload["bars"][0]["timestamp"] = "2026-10-27T09:31:00Z"
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MarketBar.objects.count(), 0)

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_stores_multiple_timeframes_from_one_telemetry_request(self):
        payload = self.payload()
        payload.pop("bars")
        payload["bars_by_timeframe"] = {
            "M1": [{
                "timestamp": "2026-10-27T09:31:00Z",
                "open": 2320.0, "high": 2322.0, "low": 2319.0, "close": 2321.0,
                "volume": 3, "closed": True,
            }],
            "M5": [{
                "timestamp": "2026-10-27T09:30:00Z",
                "open": 2320.0, "high": 2322.0, "low": 2319.0, "close": 2321.0,
                "volume": 5, "closed": True,
            }],
            "M15": [{
                "timestamp": "2026-10-27T09:30:00Z",
                "open": 2320.0, "high": 2322.0, "low": 2319.0, "close": 2321.0,
                "volume": 15, "closed": True,
            }],
            "H1": [{
                "timestamp": "2026-10-27T09:00:00Z",
                "open": 2320.0, "high": 2322.0, "low": 2319.0, "close": 2321.0,
                "volume": 60, "closed": True,
            }],
        }
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()["stored_bars"], 4)
        self.assertEqual(response.json()["stored_timeframes"], ["H1", "M1", "M15", "M5"])
        self.assertEqual(
            set(MarketBar.objects.filter(source="ea-telemetry-test").values_list("timeframe", flat=True)),
            {"M1", "M5", "M15", "H1"},
        )

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_rejects_m1_bar_shifted_by_one_second(self):
        payload = self.payload()
        payload.pop("bars")
        payload["bars_by_timeframe"] = {
            "M1": [{
                "timestamp": "2026-10-27T09:31:01Z",
                "open": 2320.0, "high": 2322.0, "low": 2319.0, "close": 2321.0,
                "volume": 1, "closed": True,
            }]
        }
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("M1 bar timestamp", response.json()["error"])
        self.assertEqual(MarketBar.objects.count(), 0)

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_rejects_h1_bar_not_on_hour_boundary(self):
        payload = self.payload()
        payload.pop("bars")
        payload["bars_by_timeframe"] = {
            "H1": [{
                "timestamp": "2026-10-27T09:05:00Z",
                "open": 2320.0, "high": 2322.0, "low": 2319.0, "close": 2321.0,
                "volume": 60, "closed": True,
            }]
        }
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("H1 bar timestamp", response.json()["error"])
        self.assertEqual(MarketBar.objects.count(), 0)
