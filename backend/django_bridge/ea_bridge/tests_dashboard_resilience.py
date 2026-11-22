"""The dashboard HTML must remain available during worker-status outages.

The dashboard renders only inert placeholders. Live worker state is retrieved
separately through the read-only strategies endpoint by browser JavaScript.
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from .strategy_registry import STRATEGIES


class DashboardResilienceTests(SimpleTestCase):
    def test_dashboard_renders_all_worker_links_without_worker_lookup(self):
        # worker_rows is intentionally NOT called by the HTML view.
        with patch("ea_bridge.strategy_views.worker_rows", side_effect=RuntimeError("simulated status outage")) as lookup:
            response = self.client.get("/api/ea/v1/dashboard")
        self.assertEqual(response.status_code, 200)
        for _, (slug, _, _) in STRATEGIES.items():
            self.assertContains(response, "/api/ea/v1/strategies/" + slug)
        self.assertContains(response, "STATUS_LOADING")
        self.assertContains(response, "Position status unavailable")
        lookup.assert_not_called()

    def test_dashboard_does_not_write_or_require_database(self):
        # SimpleTestCase prohibits DB queries, making any synchronous worker
        # or account lookup a regression, even if it happens to succeed in CI.
        response = self.client.get("/api/ea/v1/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b'class="card strategy-card'), len(STRATEGIES) + 1)
        self.assertContains(response, 'href="/api/ea/v1/scalper"')
        self.assertContains(response, 'FAST_SCALP')
