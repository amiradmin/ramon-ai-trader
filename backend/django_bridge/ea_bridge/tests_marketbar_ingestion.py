import json
from unittest.mock import Mock, patch

from django.contrib import admin
from django.test import TestCase, override_settings

from ea_bridge.models import MarketBar
from ramon_pipeline.schemas import EventKind


class MarketBarIngestionTests(TestCase):
    url = "/api/ea/v1/events"
    headers = {"HTTP_AUTHORIZATION": "Bearer test-token"}

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_PIPELINE_INGEST_ENABLED=True)
    @patch("ramon_pipeline.django_adapter.build_pipeline")
    def test_market_bar_event_is_queued_for_durable_worker_persistence(self, build_pipeline):
        pipeline = Mock()
        pipeline.accept.return_value = "1710000000000-0"
        build_pipeline.return_value = pipeline
        payload = {
            "instance_id": "marketbar-test-ea",
            "event_id": "marketbar-event-001",
            "event_type": "MARKET_BAR",
            "occurred_at": "2026-10-26T09:00:00Z",
            "symbol": "XAUUSD_o",
            "timeframe": "M15",
            "timestamp": "2026-10-26T08:45:00Z",
            "open": "2300.00",
            "high": "2302.00",
            "low": "2299.00",
            "close": "2301.50",
            "volume": "12",
        }
        response = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **self.headers
        )
        self.assertEqual(response.status_code, 200, response.content)
        envelope = pipeline.accept.call_args.args[0]
        self.assertEqual(envelope.kind, EventKind.MARKET_BAR)
        self.assertEqual(envelope.payload["symbol"], "XAUUSD_o")
        self.assertEqual(envelope.payload["close"], "2301.50")
        # The API only queues; the worker performs the MarketBar database insert.
        self.assertEqual(MarketBar.objects.count(), 0)

    def test_market_bar_is_registered_in_django_admin(self):
        self.assertIn(MarketBar, admin.site._registry)
        model_admin = admin.site._registry[MarketBar]
        self.assertIn("timestamp", model_admin.list_display)
        self.assertIn("close", model_admin.list_display)


class MarketBarWorkerIntegrityTests(TestCase):
    """An off-grid broker clock must not contaminate stored study candles."""

    def event(self, event_id, stamp, timeframe="M15"):
        return {
            "source": "integrity-test-ea",
            "event_id": event_id,
            "kind": "market_bar",
            "event_type": "MARKET_BAR",
            "occurred_at": "2026-11-19T09:30:00Z",
            "payload": json.dumps({
                "symbol": "XAUUSD_o", "timeframe": timeframe,
                "timestamp": stamp, "open": "2300",
                "high": "2302", "low": "2299", "close": "2301",
                "volume": "7", "closed": True,
            }),
        }

    def test_completed_aligned_bar_is_persisted_idempotently(self):
        from ramon_pipeline.management.commands.consume_ramon_streams import Command
        data = self.event("bar-aligned-1", "2026-11-19T09:15:00Z")
        Command._persist(data)
        Command._persist(data)
        self.assertEqual(MarketBar.objects.count(), 1)

    def test_off_grid_bar_is_quarantined_and_raw_timestamp_preserved(self):
        from ea_bridge.models import EAEvent
        from ramon_pipeline.management.commands.consume_ramon_streams import Command
        data = self.event("bar-offset-1", "2026-11-19T09:15:04Z")
        Command._persist(data)
        Command._persist(data)
        self.assertEqual(MarketBar.objects.count(), 0)
        row = EAEvent.objects.get(event_id="bar-offset-1")
        self.assertEqual(row.event_type, "MARKET_BAR_REJECTED")
        self.assertEqual(row.payload["reason"], "OFF_GRID_TIMESTAMP")
        self.assertEqual(row.payload["original_event"]["timestamp"], "2026-11-19T09:15:04Z")

    def test_m5_fractional_timestamp_is_quarantined(self):
        from ea_bridge.models import EAEvent
        from ramon_pipeline.management.commands.consume_ramon_streams import Command
        Command._persist(self.event("bar-subsecond-1", "2026-11-19T09:15:00.100000Z", "M5"))
        self.assertFalse(MarketBar.objects.exists())
        self.assertTrue(EAEvent.objects.filter(event_id="bar-subsecond-1", event_type="MARKET_BAR_REJECTED").exists())
