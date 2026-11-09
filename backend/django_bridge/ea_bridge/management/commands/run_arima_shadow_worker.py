"""Collect forward ARIMA predictions independently of the trading pipeline."""
import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from ea_bridge.arima_shadow import model_configuration, process_forecast_cycle

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Observe ARIMA forecasts and score actual next M15 closes; never authorizes orders."

    def add_arguments(self, parser):
        parser.add_argument("--poll-seconds", type=float, default=15)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        try:
            model_configuration()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        poll = options["poll_seconds"]
        if not 5 <= poll <= 60:
            raise CommandError("--poll-seconds must be between 5 and 60")
        self.stdout.write("ARIMA observer started: forecasts and accuracy only; orders are unaffected.")
        while True:
            close_old_connections()
            try:
                result = process_forecast_cycle()
                if result["processed"] or result["settled"]:
                    self.stdout.write(f"ARIMA observer: {result['processed']} processed, {result['settled']} scored/settled.")
            except Exception:
                if options["once"]:
                    raise
                log.exception("ARIMA observer cycle failed; retrying next poll")
            if options["once"]:
                return
            time.sleep(poll)
