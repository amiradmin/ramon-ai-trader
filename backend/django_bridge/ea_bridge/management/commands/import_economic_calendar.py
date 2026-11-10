"""Import a retrieved connector snapshot, without network calls or trading writes."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ea_bridge.economic_calendar import MAX_BYTES, import_snapshot


class Command(BaseCommand):
    help = "Import a TipRanks calendar snapshot for read-only dashboard context."

    def add_arguments(self, parser):
        parser.add_argument("snapshot", help="JSON envelope with retrieval time, coverage and provider payload")

    def handle(self, *args, **options):
        try:
            path = Path(options["snapshot"])
            if path.stat().st_size > MAX_BYTES:
                raise ValueError("Snapshot exceeds size limit")
            result = import_snapshot(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
            raise CommandError(f"Calendar not imported: {exc}") from exc
        self.stdout.write(f"Imported {len(result['events'])} events. Timezone verified: {result['timezone_verified']}. Context only; no orders changed.")
