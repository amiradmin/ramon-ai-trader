import logging
import time
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from ea_bridge.models import EAHeartbeat, ScalperControl
from ea_bridge.scalp_observer import run_scalp_cycle
from ea_bridge.scalper_engine import engine_cycle


class Command(BaseCommand):
    help = 'Independent demo scalper: paper collection and separately gated ticket-scoped execution.'
    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
    def handle(self,*args,**options):
        previous_results = None
        while True:
            close_old_connections()
            feed = None
            try:
                scope = dict(instance_id=settings.RAMON_SCALP_INSTANCE, account_login=settings.RAMON_SCALP_LOGIN,
                             server_name=settings.RAMON_SCALP_SERVER)
                feed = EAHeartbeat.objects.filter(**scope).first()
                if feed:
                    ScalperControl.objects.get_or_create(source=feed.instance_id,account_login=feed.account_login,
                        server_name=feed.server_name,symbol=feed.symbol)
                recorded,settled = run_scalp_cycle()
                results = engine_cycle()
                if recorded or settled or results != previous_results:
                    self.stdout.write(f'Scalper paper recorded={recorded}, settled={settled}; engine={results}')
                previous_results = list(results)
            except Exception as exc:
                try:
                    if feed:
                        from ea_bridge.scalper_audit import health_skip
                        from django.utils import timezone
                        health_skip(feed,'WORKER_ERROR_'+type(exc).__name__,timezone.now())
                except Exception:
                    logging.getLogger(__name__).exception('Could not persist scalper worker error')
                if options['once']: raise
                logging.getLogger(__name__).exception('Independent scalper cycle failed')
            if options['once']: return
            time.sleep(5)
