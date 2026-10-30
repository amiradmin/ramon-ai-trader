import json
from unittest.mock import Mock, patch

from django.contrib import admin
from django.test import TestCase, override_settings

from ea_bridge.models import MarketBar
from ramon_pipeline.schemas import EventKind
from ramon_pipeline.management.commands.consume_ramon_streams import Command


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

    def test_stream_worker_rejects_one_second_m15_timestamp_shift(self):
        fields = {
            "kind": "market_bar",
            "event_id": "worker-bar-event",
            "source": "worker-marketbar-ea",
            "event_type": "MARKET_BAR",
            "occurred_at": "2026-10-26T09:00:00+00:00",
            "payload": json.dumps({
                "symbol": "XAUUSD_o",
                "timeframe": "M15",
                "timestamp": "2026-10-26T08:45:01Z",
                "open": "2300.00",
                "high": "2302.00",
                "low": "2299.00",
                "close": "2301.50",
                "volume": "12",
            }),
        }
        with self.assertRaises(ValueError):
            Command._persist(fields)
        self.assertEqual(MarketBar.objects.count(), 0)

    def test_stream_worker_deduplicates_replayed_aligned_m15_bar(self):
        fields = {
            "kind": "market_bar",
            "event_id": "worker-bar-event-1",
            "source": "worker-marketbar-ea",
            "event_type": "MARKET_BAR",
            "occurred_at": "2026-10-26T09:00:00+00:00",
            "payload": json.dumps({
                "symbol": "XAUUSD_o",
                "timeframe": "M15",
                "timestamp": "2026-10-26T08:45:00Z",
                "open": "2300.00",
                "high": "2302.00",
                "low": "2299.00",
                "close": "2301.50",
                "volume": "12",
            }),
        }
        Command._persist(fields)
        fields["event_id"] = "worker-bar-event-2"
        Command._persist(fields)
        self.assertEqual(
            MarketBar.objects.filter(
                source="worker-marketbar-ea",
                symbol="XAUUSD_o",
                timeframe="M15",
            ).count(),
            1,
        )
