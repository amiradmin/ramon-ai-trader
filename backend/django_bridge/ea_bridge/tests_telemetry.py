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
    def test_persists_validated_symbol_info_without_authorizing_trading(self):
        payload = self.payload()
        payload["symbol_info"] = {
            "tick_size": 0.01,
            "tick_value": 1.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "stops_level_points": 0,
        }
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["trading_authorization"])
        heartbeat = EAHeartbeat.objects.get(instance_id="ea-telemetry-test")
        self.assertEqual(heartbeat.payload["symbol_info"], {
            "tick_size": "0.01",
            "tick_value": "1.0",
            "volume_min": "0.01",
            "volume_max": "100.0",
            "volume_step": "0.01",
            "stops_level_points": "0",
        })

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_rejects_invalid_symbol_info(self):
        payload = self.payload()
        payload["symbol_info"] = {
            "tick_size": 0.01,
            "tick_value": 0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "stops_level_points": 0,
        }
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EAHeartbeat.objects.filter(instance_id="ea-telemetry-test").exists())

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
    def test_rejects_misaligned_boundaries_for_all_supported_timeframes(self):
        invalid_timestamps = {
            "M1": "2026-10-27T09:30:01Z",
            "M5": "2026-10-27T09:31:00Z",
            "M15": "2026-10-27T09:31:00Z",
            "H1": "2026-10-27T10:01:00Z",
        }
        for timeframe, timestamp in invalid_timestamps.items():
            with self.subTest(timeframe=timeframe):
                payload = self.payload()
                payload["bars"] = []
                payload["bar_sets"] = [{
                    "timeframe": timeframe,
                    "bars": [{
                        "timestamp": timestamp,
                        "open": 2320.0,
                        "high": 2322.0,
                        "low": 2319.0,
                        "close": 2321.0,
                        "volume": 12,
                        "closed": True,
                    }],
                }]
                response = self.client.post(
                    self.url, data=json.dumps(payload),
                    content_type="application/json", **self.headers,
                )
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(MarketBar.objects.count(), 0)
        self.assertFalse(EAHeartbeat.objects.filter(instance_id="ea-telemetry-test").exists())

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_accepts_exact_m1_m5_m15_and_h1_boundaries(self):
        payload = self.payload()
        payload["bars"] = []
        payload["bar_sets"] = [
            {"timeframe": "M1", "bars": [{
                "timestamp": "2026-10-27T09:30:00Z", "open": 2320, "high": 2322,
                "low": 2319, "close": 2321, "volume": 12, "closed": True,
            }]},
            {"timeframe": "M5", "bars": [{
                "timestamp": "2026-10-27T09:30:00Z", "open": 2320, "high": 2322,
                "low": 2319, "close": 2321, "volume": 12, "closed": True,
            }]},
            {"timeframe": "M15", "bars": [{
                "timestamp": "2026-10-27T09:30:00Z", "open": 2320, "high": 2322,
                "low": 2319, "close": 2321, "volume": 12, "closed": True,
            }]},
            {"timeframe": "H1", "bars": [{
                "timestamp": "2026-10-27T09:00:00Z", "open": 2320, "high": 2322,
                "low": 2319, "close": 2321, "volume": 12, "closed": True,
            }]},
        ]
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(MarketBar.objects.filter(source="ea-telemetry-test").count(), 4)

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_rejects_m15_microsecond_jitter(self):
        payload = self.payload()
        payload["bars"][0]["timestamp"] = "2026-10-27T09:30:00.000001Z"
        response = self.client.post(
            self.url, data=json.dumps(payload),
            content_type="application/json", **self.headers,
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(MarketBar.objects.count(), 0)
