from copy import deepcopy
from datetime import datetime, timedelta, timezone as tz
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from .economic_calendar import import_snapshot, snapshot_path

NOW = datetime(2026, 11, 9, 9, tzinfo=tz.utc)


class EconomicCalendarTests(SimpleTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = override_settings(MEDIA_ROOT=self.directory.name)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.data = {"provider": "TipRanks", "retrieved_at": NOW.isoformat(),
            "from_date": "2026-11-09", "to_date": "2026-11-16",
            "payload": {"returned": 1, "totalMatched": 1, "economicCalendar": [
                {"time": "2026-11-10T13:30:00", "event": "CPI", "country": "US", "impact": "High",
                 "actual": None, "estimate": 0.3, "prev": 0.2, "unit": "%"}]}}

    def status(self, now=NOW):
        with patch("ea_bridge.economic_calendar.timezone.now", return_value=now):
            return self.client.get("/api/ea/v1/research/economic-calendar").json()

    def test_missing_snapshot_is_explicit_and_get_never_creates_files(self):
        self.assertEqual(self.status()["state"], "NOT_IMPORTED")
        self.assertFalse(snapshot_path().exists())

    def test_naive_provider_time_is_never_assumed_utc(self):
        import_snapshot(self.data, now=NOW)
        data = self.status()
        self.assertEqual(data["state"], "TIMEZONE_UNVERIFIED")
        self.assertIsNone(data["events"][0]["time_utc"])
        self.assertEqual(data["events"][0]["unit"], "%")
        self.assertFalse(data["affects_orders"])
        self.assertFalse(data["automatic_refresh"])

    def test_explicit_offset_converts_to_utc_and_past_events_are_removed(self):
        self.data["payload"]["economicCalendar"][0]["time"] = "2026-11-10T17:00:00+03:30"
        import_snapshot(self.data, now=NOW)
        data = self.status()
        self.assertEqual(data["state"], "READY")
        self.assertEqual(data["events"][0]["time_utc"], "2026-11-10T13:30:00+00:00")
        with patch("ea_bridge.economic_calendar.MAX_AGE", timedelta(days=7)):
            self.assertEqual(self.status(NOW+timedelta(days=2))["events"], [])

    def test_stale_snapshot_is_not_described_as_current(self):
        import_snapshot(self.data, now=NOW)
        self.assertEqual(self.status(NOW+timedelta(hours=25))["state"], "STALE")

    def test_invalid_or_incomplete_import_preserves_last_good_snapshot(self):
        import_snapshot(self.data, now=NOW)
        previous = snapshot_path().read_bytes()
        for change in ("future", "incomplete", "nan", "country", "coverage"):
            bad = deepcopy(self.data)
            if change == "future":
                bad["retrieved_at"] = (NOW+timedelta(hours=1)).isoformat()
            elif change == "incomplete":
                bad["payload"]["totalMatched"] = 2
            elif change == "nan":
                bad["payload"]["economicCalendar"][0]["actual"] = float("nan")
            elif change == "country":
                bad["payload"]["economicCalendar"][0]["country"] = "UK"
            else:
                bad["to_date"] = "2026-11-09"
            with self.subTest(change=change), self.assertRaises(ValueError):
                import_snapshot(bad, now=NOW)
            self.assertEqual(snapshot_path().read_bytes(), previous)

    def test_corrupt_file_and_post_are_rejected(self):
        snapshot_path().parent.mkdir(parents=True)
        snapshot_path().write_text("bad json")
        self.assertEqual(self.status()["state"], "UNAVAILABLE")
        self.assertEqual(self.client.post("/api/ea/v1/research/economic-calendar").status_code, 405)

    def test_command_imports_the_envelope_and_rejects_large_input(self):
        source = Path(self.directory.name)/"input.json"
        source.write_text(json.dumps(self.data))
        with patch("ea_bridge.economic_calendar.timezone.now", return_value=NOW):
            call_command("import_economic_calendar", str(source), verbosity=0)
        self.assertTrue(snapshot_path().exists())
        source.write_text(" "*512001)
        with self.assertRaises(CommandError):
            call_command("import_economic_calendar", str(source))
