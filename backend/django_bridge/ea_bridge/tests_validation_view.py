from datetime import datetime, timezone
from django.test import TestCase
from django.urls import reverse
from .models import MarketBar


class ValidationViewTests(TestCase):
    def test_read_only_and_source_isolated(self):
        for source in ('chosen', 'other'):
            MarketBar.objects.create(source=source, symbol='XAUUSD_o', timeframe='M15',
                timestamp=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
                open=2, high=3, low=1, close=2, volume=1)
        response = self.client.get(reverse('ramon-strategy-validation'), {'source': 'chosen', 'year': '2026'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'INVALID')
        self.assertContains(response, 'NO_DATA')
        self.assertEqual(MarketBar.objects.count(), 2)
        self.assertNotContains(response, 'other')
        self.assertEqual(self.client.post(reverse('ramon-strategy-validation')).status_code, 405)

    def test_invalid_year(self):
        response = self.client.get(reverse('ramon-strategy-validation'), {'year': '2026x'})
        self.assertEqual(response.status_code, 400)
