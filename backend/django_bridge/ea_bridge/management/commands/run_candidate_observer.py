import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from ea_bridge.candidate_observer import settle_candidates
from ea_bridge.plan_observer import settle_plans


class Command(BaseCommand):
    help = "Settle forward candidate price observations without creating trading commands."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                count = settle_candidates()
                plans = settle_plans()
                if count:
                    self.stdout.write(f"Settled {count} candidate observations")
                if plans:
                    self.stdout.write(f"Settled {plans} stop/target plans")
            except Exception:
                if options["once"]:
                    raise
                logging.getLogger(__name__).exception("Candidate observer cycle failed")
            if options["once"]:
                return
            time.sleep(30)
