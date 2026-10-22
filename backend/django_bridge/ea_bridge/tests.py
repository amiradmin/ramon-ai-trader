import json
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import EAEvent, MarketBar, OrderIntentRecord


class EAIngestionTests(TestCase):
    def setUp(self):
        self.url = "/api/ea/v1/events"
        self.headers = {"HTTP_AUTHORIZATION": "Bearer test-token"}
        self.payload = {"instance_id": "ea-test-1", "event_id": "evt-test-1", "event_type": "EXECUTION_FILL",
                        "occurred_at": "2026-10-22T10:00:00Z", "ticket": "123"}

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_PIPELINE_INGEST_ENABLED=False)
    def test_legacy_durable_ingestion_remains_default(self):
        response = self.client.post(self.url, data=json.dumps(self.payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["accepted"])
        self.assertTrue(EAEvent.objects.filter(event_id="evt-test-1").exists())

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_PIPELINE_INGEST_ENABLED=True)
    @patch("ramon_pipeline.django_adapter.build_pipeline")
    def test_opt_in_pipeline_queues_without_synchronous_event_insert(self, build_pipeline):
        pipeline = Mock()
        pipeline.accept.return_value = "1710000000000-0"
        build_pipeline.return_value = pipeline
        response = self.client.post(self.url, data=json.dumps(self.payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["queued"])
        pipeline.accept.assert_called_once()
        self.assertFalse(EAEvent.objects.filter(event_id="evt-test-1").exists())

    @override_settings(EA_BEARER_TOKEN="test-token", RAMON_PIPELINE_INGEST_ENABLED=True)
    @patch("ramon_pipeline.django_adapter.build_pipeline", side_effect=ConnectionError("redis unavailable"))
    def test_pipeline_returns_retryable_error_when_redis_is_unavailable(self, _build_pipeline):
        response = self.client.post(self.url, data=json.dumps(self.payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "2")
        self.assertFalse(EAEvent.objects.filter(event_id="evt-test-1").exists())

    @override_settings(EA_BEARER_TOKEN="test-token")
    def test_rejects_timezone_naive_event_timestamp(self):
        payload = dict(self.payload, occurred_at="2026-10-22T10:00:00")
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 400)

    @override_settings(EA_BEARER_TOKEN="")
    def test_rejects_when_token_is_not_configured(self):
        response = self.client.post(self.url, data=json.dumps(self.payload), content_type="application/json")
        self.assertEqual(response.status_code, 503)
