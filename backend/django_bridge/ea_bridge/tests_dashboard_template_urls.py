"""Guard against dashboard 500s caused by missing named URL routes.

Django validates static {% url %} tags only when a template is rendered, not
during manage.py check. This test ensures every dashboard template link resolves.
"""
from django.test import SimpleTestCase
from django.template.loader import render_to_string


class DashboardTemplateURLTests(SimpleTestCase):
    def test_main_dashboard_named_urls_resolve_without_database(self):
        html = render_to_string(
            "ea_bridge/ramon_opportunity_risk_dashboard.html",
            {"strategy_workers": []},
        )
        self.assertIn("Independent Strategy Desk", html)

    def test_learning_and_forensics_templates_render(self):
        for template in (
            "ea_bridge/learning_dashboard.html",
            "ea_bridge/trade_forensics.html",
        ):
            with self.subTest(template=template):
                content = render_to_string(template)
                self.assertTrue(content)
