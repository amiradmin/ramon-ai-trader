"""Regression tests: live settings persist but never change simulator or broker facts."""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .decision import _build_pipeline
from .live_strategy_settings import active_settings, profile_key
from .models import DecisionThresholdConfig, EAHeartbeat, StrategyWorkerControl, ScalperControl


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
        self.client.force_login(self.user)
        with patch("ea_bridge.live_strategy_settings.configured_feed", return_value=self.feed), \
             patch("ea_bridge.live_strategy_settings.real_cent_scope", return_value=(True, "OK")):
            response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["broker"]["symbol"], "XAUUSD_l")
        self.assertEqual(response.json()["broker"]["symbol_spec"]["tick_size"], 0.01)
        self.assertFalse(DecisionThresholdConfig.objects.filter(profile=profile_key(self.setup)).exists())

    def test_staff_required_and_scope_validation(self):
        with patch("ea_bridge.live_strategy_settings.configured_feed", return_value=self.feed), \
             patch("ea_bridge.live_strategy_settings.real_cent_scope", return_value=(True, "OK")):
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
        with patch("ea_bridge.live_strategy_settings.configured_feed", return_value=self.feed), \
             patch("ea_bridge.live_strategy_settings.real_cent_scope", return_value=(True, "OK")):
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

    def test_rejects_risk_increase_above_strategy_ceiling(self):
        self.client.force_login(self.user)
        self.payload["overrides"]["maximum_risk_percent"] = 1.55
        with patch("ea_bridge.live_strategy_settings.configured_feed", return_value=self.feed), \
             patch("ea_bridge.live_strategy_settings.real_cent_scope", return_value=(True, "OK")):
            response = self.client.post(self.endpoint, json.dumps(self.payload),
                                        content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(DecisionThresholdConfig.objects.filter(profile=profile_key(self.setup)).exists())

    def test_explicit_trend_risk_and_advanced_fields_are_scoped(self):
        self.client.force_login(self.user)
        self.payload["overrides"].update(maximum_risk_percent=1.5, max_chase_atr=0.85,
                                         max_signal_age_bars=2, max_total_risk_percent=2)
        with patch("ea_bridge.live_strategy_settings.configured_feed", return_value=self.feed), \
             patch("ea_bridge.live_strategy_settings.real_cent_scope", return_value=(True, "OK")):
            response = self.client.post(self.endpoint, json.dumps(self.payload), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        live = _build_pipeline(strategy_setup=self.setup, live_scope=self.scope)
        self.assertEqual(live.risk_manager.maximum_risk_percent, 1.5)
        self.assertEqual(live.entry_timer.config.max_chase_atr, 0.85)
        self.assertEqual(live.entry_timer.config.max_signal_age_bars, 2)
        self.assertEqual(live.risk_manager.config.max_total_risk_percent, 2)
        self.assertEqual(_build_pipeline(strategy_setup=self.setup).risk_manager.maximum_risk_percent, 0.5)

    @override_settings(RAMON_SCALP_INSTANCE="real-feed", RAMON_SCALP_LOGIN="556677",
                       RAMON_SCALP_SERVER="CentServer")
    def test_fast_scalp_uses_existing_worker_control_and_hard_cap(self):
        scalp = ScalperControl.objects.create(
            source="real-feed", account_login="556677", server_name="CentServer",
            symbol="XAUUSD_l", automatic=False
        )
        self.client.force_login(self.user)
        endpoint = "/api/ea/v1/dashboard/live-scalper-risk"
        with patch("ea_bridge.live_strategy_settings.real_cent_scope", return_value=(True, "OK")):
            # The configured scalper ceiling is 5.000%; shared exposure
            # is separately enforced by the execution engine.
            original_risk = scalp.risk_percent
            over = self.client.post(endpoint, json.dumps({
                "scope": self.scope, "risk_percent": "5.001"
            }), content_type="application/json")
            self.assertEqual(over.status_code, 400)
            scalp.refresh_from_db()
            self.assertEqual(scalp.risk_percent, original_risk)
            good = self.client.post(endpoint, json.dumps({
                "scope": self.scope, "risk_percent": "0.075"
            }), content_type="application/json")
        self.assertEqual(good.status_code, 200, good.content)
        scalp.refresh_from_db()
        self.assertEqual(str(scalp.risk_percent), "0.075")
        self.assertFalse(scalp.automatic)
