import json
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from ea_bridge.regime_coordinator import build_report

class Command(BaseCommand):
    help="Read-only advisory regime and candidate report for the configured strategy account."
    def add_arguments(self,p):
        p.add_argument("--symbol",required=True)
    def handle(self,*args,**opts):
        scope=[getattr(settings,k,"") for k in ("RAMON_STRATEGY_INSTANCE","RAMON_STRATEGY_LOGIN","RAMON_STRATEGY_SERVER")]
        if not all(scope):
            raise CommandError("Configured strategy account scope is incomplete")
        self.stdout.write(json.dumps(build_report(scope[0],opts["symbol"],scope[1],scope[2]),indent=2,allow_nan=False))
