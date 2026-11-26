import json
from django.core.management.base import BaseCommand, CommandError
from ea_bridge.execution_quality import build_report
class Command(BaseCommand):
    help="Read-only V14.2 execution quality report"
    def add_arguments(self,p):
        p.add_argument("--days",type=int,default=30)
    def handle(self,*args,**opts):
        try:
            result=build_report(opts["days"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result,indent=2,allow_nan=False))
