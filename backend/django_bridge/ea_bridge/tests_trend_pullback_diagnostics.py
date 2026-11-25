from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch
from .models import EACommand, DecisionEvaluation
from .trend_pullback_diagnostics import build_report


class TrendPullbackDiagnosticTests(TestCase):
    @patch("ea_bridge.trend_pullback_diagnostics.configured_feed", return_value=None)
    def test_no_feed_and_read_only(self, feed):
        before = (EACommand.objects.count(), DecisionEvaluation.objects.count())
        report = build_report()
        self.assertEqual(report["readiness"], "CONFIGURED_FEED_MISSING")
        self.assertEqual(report["strategy"], "TREND_PULLBACK")
        self.assertEqual(before, (EACommand.objects.count(), DecisionEvaluation.objects.count()))

    @patch("ea_bridge.trend_pullback_diagnostics.configured_feed", return_value=None)
    def test_staff_get_only(self, feed):
        url = reverse("ramon-trend-pullback-diagnostic-report")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user(username="nonstaff"))
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user(username="staff", is_staff=True))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("ramon-trend-pullback-diagnostic-", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.client.post(url).status_code, 405)
