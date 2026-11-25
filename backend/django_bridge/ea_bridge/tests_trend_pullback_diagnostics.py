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


    def test_compact_features_removes_repeated_calculations(self):
        from .trend_pullback_diagnostics import compact_features
        result = compact_features({"stages": {"opportunities": {"reason": "WAIT", "checks": [
            {"name": "impulse", "passed": False, "calculation": {"large": "x" * 10000}},
            {"name": "score", "status": "NOT_EVALUATED"}]}}})
        self.assertEqual(result["stages"]["opportunities"]["checks"], [{"name": "impulse", "passed": False}])

    def test_per_bar_counts_latest_only(self):
        from .models import RiskVerdictRecord
        from .trend_pullback_diagnostics import per_bar_counts
        for i, bar in enumerate((100, 100, 200)):
            verdict = RiskVerdictRecord.objects.create(decision_id=str(i), reason_code="WAIT")
            DecisionEvaluation.objects.create(decision_id=str(i), instance_id="test",
                account_login="1", server_name="test", symbol="XAUUSD_l", action="WAIT",
                rationale_code=str(i), confidence=0, model_version="test",
                features={"market_snapshot": {"bar_time": bar}}, verdict=verdict)
        report = per_bar_counts(DecisionEvaluation.objects.all())
        self.assertEqual(report["unique_bars"], 2)
        self.assertEqual({r["rationale_code"] for r in report["counts"]}, {"1", "2"})
