"""Regression tests: live settings persist but never change simulator or broker facts."""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from .decision import _build_pipeline
from .live_strategy_settings import active_settings, profile_key
from .models import DecisionThresholdConfig, EAHeartbeat, StrategyWorkerControl


class LiveStrategySettingsTests(TestCase):
    endpoint = "/api/ea/v1/dashboard/live-strategy-settings"
    setup = "TREND_PULLBACK"

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ramon-config-staff", password="test-local-only", is_staff=True
        )
        self.feed = EAHeartbeat.objects.create(
            instance_id="real-feed", account_login="556677", server_name="CentServer",
            symbol="XAUUSD_l", status="TELEMETRY_OK", terminal_connected=True,
            payload={
                "account": {"balance": 1000, "equity": 998, "free_margin": 960, "currency": "USD"},
                "symbol_info": {"point": 0.01, "tick_size": 0.01, "tick_value": 1,
                                "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
            },
        )
        self.control = StrategyWorkerControl.objects.create(
            setup=self.setup, source="real-feed", account_login="556677",
            server_name="CentServer", symbol="XAUUSD_l"
        )
        self.scope = ["real-feed", "556677", "CentServer", "XAUUSD_l"]
        self.payload = {
            "setup": self.setup, "scope": self.scope,
            "overrides": {"minimum_score": 0.6, "minimum_quality": 0.7,
                          "maximum_spread_points": 42, "maximum_risk_percent": 0.1},
        }

    def test_read_does_not_change_live_risk_or_start_positions(self):
        with patch("ea_bridge.live_strategy_settings.configured_feed", return_value=self.feed):
            response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["broker"]["symbol"], "XAUUSD_l")
        self.assertEqual(response.json()["broker"]["symbol_spec"]["tick_size"], 0.01)
        self.assertFalse(DecisionThresholdConfig.objects.filter(profile=profile_key(self.setup)).exists())

    def test_staff_required_and_scope_validation(self):
        with patch("ea_bridge.live_strategy_settings.configured_feed", return_value=self.feed):
            response = self.client.post(self.endpoint, json.dumps(self.payload),
                                        content_type="application/json")
            self.assertNotEqual(response.status_code, 200)
            self.client.force_login(self.user)
            bad = dict(self.payload, scope=["wrong", "556677", "CentServer", "XAUUSD_l"])
            response = self.client.post(self.endpoint, json.dumps(bad),
                                        content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(DecisionThresholdConfig.objects.filter(profile=profile_key(self.setup)).exists())

    def test_save_applies_only_to_worker_scope_not_simulator(self):
        self.client.force_login(self.user)
        with patch("ea_bridge.live_strategy_settings.configured_feed", return_value=self.feed):
            response = self.client.post(self.endpoint, json.dumps(self.payload),
                                        content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(active_settings(self.setup, self.scope)["maximum_spread_points"], 42)
        self.assertEqual(active_settings(self.setup, ["other", "556677", "CentServer", "XAUUSD_l"]), {})
        live = _build_pipeline(strategy_setup=self.setup, demo_mode=False, live_scope=self.scope)
        research = _build_pipeline(strategy_setup=self.setup, demo_mode=False)
        self.assertEqual(live.entry_timer.maximum_spread_points, 42)
        self.assertNotEqual(research.entry_timer.maximum_spread_points, 42)
        self.assertLessEqual(live.risk_manager.maximum_risk_percent, 0.5)
        self.assertTrue(self.control.automatic)

    def test_rejects_risk_increase_above_shared_ceiling(self):
        self.client.force_login(self.user)
        self.payload["overrides"]["maximum_risk_percent"] = 1.5
        with patch("ea_bridge.live_strategy_settings.configured_feed", return_value=self.feed):
            response = self.client.post(self.endpoint, json.dumps(self.payload),
                                        content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(DecisionThresholdConfig.objects.filter(profile=profile_key(self.setup)).exists())
