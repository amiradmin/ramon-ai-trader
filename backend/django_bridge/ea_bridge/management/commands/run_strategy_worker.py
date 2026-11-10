import logging
import os
from pathlib import Path
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone
from ea_bridge.models import StrategyWorkerControl
from ea_bridge.strategy_registry import STRATEGIES
from ea_bridge.strategy_worker import worker_cycle


class Command(BaseCommand):
    help='Run exactly one independent M15 setup with its own decisions and orders.'

    def add_arguments(self,parser):
        parser.add_argument('--setup',default=os.environ.get('RAMON_STRATEGY_SETUP'),choices=list(STRATEGIES))
        parser.add_argument('--once',action='store_true')

    def handle(self,*args,**options):
        setup=options['setup']
        if setup not in STRATEGIES: raise CommandError('One known --setup is required')
        previous=None
        while True:
            close_old_connections()
            try:
                state=worker_cycle(setup)
                Path('/tmp/strategy-worker-heartbeat').write_text(setup)
                if state!=previous:
                    self.stdout.write(f'{setup}: {state}'); previous=state
            except Exception as exc:
                logging.getLogger(__name__).exception('Strategy %s failed',setup)
                StrategyWorkerControl.objects.filter(setup=setup).update(last_seen=timezone.now(),state='WORKER_ERROR_'+type(exc).__name__)
                if options['once']: raise
            if options['once']: return
            time.sleep(5)
