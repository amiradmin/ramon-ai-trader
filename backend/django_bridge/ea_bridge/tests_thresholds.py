import json

from django.test import TestCase

from ea_bridge.models import EAEvent, EAHeartbeat


class RiskThresholdDashboardTests(TestCase):
    def setUp(self):
        EAHeartbeat.objects.create(
            instance_id="threshold-demo-ea",
            account_login="12345678",
            server_name="Demo-Server",
            symbol="XAUUSD_o",
            payload={"account_trade_mode": 0},
        )

    def test_demo_profile_exposes_requested_editable_risk_defaults(self):
        response = self.client.get("/api/ea/v1/decision-thresholds")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        risk = data["values"]["risk_management"]
        self.assertEqual(risk["min_stop_atr"], 0.6)
        self.assertEqual(risk["max_stop_atr"], 3.0)
        self.assertEqual(risk["min_reward_risk"], 1.2)
        self.assertEqual(risk["target_reward_risk"], 1.25)
        self.assertEqual(risk["stop_buffer_atr"], 0.2)
        self.assertEqual(risk["maximum_risk_percent"], 1.6)
        self.assertEqual(risk["max_daily_loss_percent"], 5.0)
        self.assertEqual(risk["max_total_risk_percent"], 2.0)

    def test_dashboard_can_save_all_risk_thresholds_and_audit_actor_and_time(self):
        values = {
            "opportunities": {},
            "entry_timing": {},
            "risk_management": {
                "min_stop_atr": 0.7,
                "max_stop_atr": 3.2,
                "min_reward_risk": 1.2,
                "target_reward_risk": 1.4,
                "stop_buffer_atr": 0.25,
                "maximum_risk_percent": 1.5,
                "max_daily_loss_percent": 4.5,
                "max_total_risk_percent": 2.0,
            },
        }
        response = self.client.post(
            "/api/ea/v1/decision-thresholds",
            data=json.dumps({"values": values}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["values"]["risk_management"], values["risk_management"])
        self.assertEqual(data["audit"]["action"], "update")
        self.assertTrue(data["audit"]["actor"])
        self.assertTrue(data["audit"]["changed_at"])
        event = EAEvent.objects.get(event_type="THRESHOLD_CONFIG_UPDATED")
        self.assertEqual(event.payload["profile"], "demo")
        self.assertEqual(event.payload["values"]["risk_management"], values["risk_management"])

        readback = self.client.get("/api/ea/v1/decision-thresholds")
        self.assertEqual(json.loads(readback.content)["values"]["risk_management"], values["risk_management"])
        self.assertEqual(json.loads(readback.content)["audit"]["actor"], data["audit"]["actor"])

    def test_risk_threshold_validation_keeps_hard_bounds(self):
        response = self.client.post(
            "/api/ea/v1/decision-thresholds",
            data=json.dumps({"values": {"risk_management": {"maximum_risk_percent": 2.01}}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EAEvent.objects.filter(event_type="THRESHOLD_CONFIG_UPDATED").exists())
