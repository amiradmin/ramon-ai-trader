import json
from django.test import TestCase, override_settings
from .models import EAEvent, EAHeartbeat

@override_settings(EA_BEARER_TOKEN="test-token-for-tests-only")
class EABridgeTests(TestCase):
    def setUp(self):
        self.auth = {"HTTP_AUTHORIZATION": "Bearer test-token-for-tests-only"}

    def test_health_does_not_require_ea_auth(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_heartbeat_requires_token(self):
        response = self.client.post("/api/ea/v1/heartbeat", data=json.dumps({"instance_id":"test"}), content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_heartbeat_upserts_instance(self):
        payload = {"instance_id":"acct-XAUUSD_o-26092212", "symbol":"XAUUSD_o", "ea_version":"0.60.1", "terminal_connected":True}
        response = self.client.post("/api/ea/v1/heartbeat", data=json.dumps(payload), content_type="application/json", **self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EAHeartbeat.objects.get().symbol, "XAUUSD_o")
        self.assertTrue(response.json()["ok"])

    def test_events_are_idempotent(self):
        payload = {"instance_id":"ea-1", "event_id":"event-1", "event_type":"ORDER_FILLED"}
        first = self.client.post("/api/ea/v1/events", data=json.dumps(payload), content_type="application/json", **self.auth)
        second = self.client.post("/api/ea/v1/events", data=json.dumps(payload), content_type="application/json", **self.auth)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["accepted"])
        self.assertFalse(second.json()["accepted"])
        self.assertEqual(EAEvent.objects.count(), 1)
