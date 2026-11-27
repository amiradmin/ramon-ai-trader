import fcntl
import json
import math
import time
from pathlib import Path
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from ea_bridge.models import EAHeartbeat, MarketBar
from ea_bridge.account_scope import real_cent_scope
from ea_bridge.execution_observer import number
from ea_bridge.forward_profiles import PROFILES, VERSION, new_state, cycle, metrics
from .run_research_shadow import save, row_dict

def observed_quote(config, now):
    feed=EAHeartbeat.objects.filter(instance_id=config["source"],symbol=config["symbol"]).first()
    ok,_=real_cent_scope(feed)
    if not ok or not feed.terminal_connected or feed.status!="TELEMETRY_OK" or not 0<=(now-feed.last_seen).total_seconds()<=60:return None
    p=feed.payload
    try:
        stamp=parse_datetime(str(p.get("quote_time","")))
        q=p["quote"]; spec=p["symbol_info"]
        if not stamp or not stamp.tzinfo:return None
        bid=number(q.get("bid"),positive=True); ask=number(q.get("ask"),positive=True)
        if bid is None or ask is None:return None
        for k,actual in (("point",q.get("point")),("tick_size",spec.get("tick_size")),("tick_value",spec.get("tick_value")),("volume_min",spec.get("volume_min")),("volume_step",spec.get("volume_step")),("volume_max",spec.get("volume_max")),("stop_level_points",spec.get("stops_level_points"))):
            v=number(actual)
            if v is None or not math.isclose(v,config[k],rel_tol=1e-8,abs_tol=1e-9):return None
        return dict(time=stamp.timestamp(),bid=bid,ask=ask)
    except (ValueError,TypeError,KeyError,AttributeError):return None

class Command(BaseCommand):
    help="V15.3 paper profiles using observed quotes; independent from broker execution."
    def add_arguments(self,p):
        for k in ("source","symbol","state-file"):p.add_argument("--"+k,required=True)
        for k in ("balance","point","tick-size","tick-value","volume-min","volume-step","volume-max"):p.add_argument("--"+k,required=True,type=float)
        for k,v in (("max-spread-points",80),("slippage-points",5),("commission-per-lot",5),("stop-level-points",0)):
            p.add_argument("--"+k,type=float,default=v)
        p.add_argument("--poll-seconds",type=int,default=5)
        p.add_argument("--once",action="store_true")
    def handle(self,*args,**opts):
        keys=("source","symbol","balance","point","tick_size","tick_value","volume_min","volume_step","volume_max","max_spread_points","slippage_points","commission_per_lot","stop_level_points")
        config={k:opts[k] for k in keys}
        if any(getattr(settings,k,False) for k in ("RAMON_EA_COMMAND_CREATION_ENABLED","RAMON_EA_COMMAND_EXECUTION_ENABLED","RAMON_SCALP_EXECUTION_ENABLED","RAMON_REAL_EXECUTION_ENABLED")):
            raise CommandError("Forward worker requires execution flags disabled")
        if any(not math.isfinite(config[k]) or config[k]<=0 for k in keys[2:10]) or any(not math.isfinite(config[k]) or config[k]<0 for k in keys[10:]) or config["volume_min"]>config["volume_max"] or not 1<=opts["poll_seconds"]<=10:
            raise CommandError("Invalid paper configuration")
        path=Path(opts["state_file"])
        with path.with_suffix(path.suffix+".lock").open("a") as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise CommandError("Forward cohort already locked")
            if path.exists():
                state=json.loads(path.read_text())
                if state.get("version")!=VERSION or state.get("mode")!="PAPER_ONLY" or state.get("live_promotion") is not False or state.get("config")!=config or state.get("profiles")!={k:list(v) for k,v in PROFILES.items()}:
                    raise CommandError("Cohort mismatch; use a new file")
            else:state=new_state(config,timezone.now().isoformat())
            while True:
                now=timezone.now()
                quote=observed_quote(config,now)
                rows=[row_dict(r) for r in MarketBar.objects.filter(source=config["source"],symbol=config["symbol"],timeframe="M15",
                    timestamp__lte=now-timedelta(minutes=15),received_at__lte=now).order_by("-timestamp")[:256]][::-1]
                status=cycle(state,rows,quote,now.timestamp())
                state.update(status=status,updated_at=now.isoformat())
                metrics(state)
                save(path,state)
                self.stdout.write(status)
                if opts["once"]:return
                time.sleep(opts["poll_seconds"])
