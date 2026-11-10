"""Refresh the shared schedule without any trading or database-writing capability."""
import json
import os
import time
from pathlib import Path
from django.core.management.base import BaseCommand
from django.utils import timezone
from ea_bridge.calendar_feed import refresh_calendar, recent_snapshot


class Command(BaseCommand):
    help = 'Refresh the verified-offset Forex Factory weekly calendar every 15 minutes.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        if not options['once']:
            cached = recent_snapshot()
            if cached:
                marker = Path('/tmp/calendar-worker-success')
                marker.write_text(cached['retrieved_at'])
                from datetime import datetime
                stamp = datetime.fromisoformat(cached['retrieved_at']).timestamp()
                os.utime(marker, (stamp, stamp))
                self.stdout.write(json.dumps(dict(event='calendar_recent_snapshot_reused', **cached)))
                time.sleep(cached['wait_seconds'])
        while True:
            try:
                result = refresh_calendar()
                Path('/tmp/calendar-worker-success').write_text(timezone.now().isoformat())
                self.stdout.write(json.dumps(dict(event='calendar_refresh_success', **result)))
            except Exception as exc:
                # Do not overwrite the last valid schedule or advance its receipt timestamp.
                self.stderr.write(json.dumps(dict(event='calendar_refresh_failed',
                    timestamp=timezone.now().isoformat(), error_type=type(exc).__name__)))
                if options['once']:
                    raise
            if options['once']:
                return
            time.sleep(900)
