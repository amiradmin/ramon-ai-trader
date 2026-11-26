import json
from django.core.management.base import BaseCommand, CommandError
from ea_bridge.real_performance import build_report

class Command(BaseCommand):
    help="Read-only real account recorded-outcome performance report"
    def add_arguments(self,p):
        p.add_argument("--days",type=int,default=30)
    def handle(self,*args,**opts):
        try:
            report=build_report(opts["days"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report,indent=2,allow_nan=False))
