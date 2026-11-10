import logging
from pathlib import Path
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from ea_bridge.trend_observer import run_trend_cycle, health


class Command(BaseCommand):
    help = 'Collect H1/H4 paper trades only; never issue broker commands.'

    def add_arguments(self, parser):
        parser.add_argument('--once',action='store_true')

    def handle(self,*args,**options):
        while True:
            close_old_connections()
            try:
                recorded,settled=run_trend_cycle()
                Path('/tmp/trend-worker-heartbeat').touch()
                if recorded or settled or options['once']:
                    self.stdout.write(f'Trend PAPER_ONLY recorded={recorded} settled={settled}')
            except Exception as exc:
                logging.getLogger(__name__).exception('Trend paper cycle failed')
                health('WORKER_ERROR_'+type(exc).__name__,timezone.now())
                if options['once']: raise
            if options['once']: return
            time.sleep(15)
