"""Single-process worker for historical research jobs; no MT5/EA transport."""
import json
import time
from datetime import date
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from ea_bridge.replay_backtest import run_backtest_data
from ea_bridge.replay_jobs import ROOT, read, store

class Command(BaseCommand):
    help = "Process isolated historical replay backtests (never create MT5 commands)."

    def handle(self, *args, **options):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.stdout.write("Historical research worker running; live trade execution is not used.")
        # A prior worker may have exited mid-run. Recompute from the beginning;
        # no broker commands, positions, or mutable live state are involved.
        for file in ROOT.glob("*.json"):
            try:
                orphan = read(file.stem)
                if orphan.get("status") == "RUNNING":
                    orphan["status"] = "QUEUED"
                    orphan["progress_pct"] = 0
                    orphan["updated_at"] = time.time()
                    store(orphan["id"], orphan)
            except (OSError, ValueError, KeyError):
                continue
        while True:
            close_old_connections()
            pending = []
            for path in ROOT.glob("*.json"):
                try:
                    job = read(path.stem)
                except (ValueError, OSError):
                    continue
                if job.get("status") == "QUEUED":
                    pending.append(job)
            if not pending:
                time.sleep(2)
                continue
            job = min(pending, key=lambda j:j["created_at"])
            job_id = job["id"]
            job["status"] = "RUNNING"
            job["updated_at"] = time.time()
            store(job_id,job)
            try:
                data = dict(job["params"])
                data["start"] = date.fromisoformat(data["start"])
                data["end"] = date.fromisoformat(data["end"])
                def progress(current,total):
                    job["progress_pct"]=min(99,round(100*current/max(1,total)))
                    job["updated_at"]=time.time()
                    store(job_id,job)
                compare=bool(job.get("compare_balances"))
                primary_balance=data["balance"]
                def run_one(balance,offset,scale):
                    trial={**data,"balance":balance}
                    def scaled(done,total):
                        progress(offset+scale*done/max(1,total),100)
                    response=run_backtest_data(trial,max_candles=16000,progress=scaled)
                    payload=json.loads(response.content)
                    if response.status_code!=200:
                        raise ValueError(payload.get("error","Historical backtest failed"))
                    return payload
                if compare:
                    # The requested balance remains the primary result. Both
                    # fixed-balance comparisons use exactly the same input
                    # parameters and validated OHLC fingerprint.
                    result=run_one(primary_balance,0,33)
                    low=result if primary_balance==1000.0 else run_one(1000.0,33,33)
                    high=result if primary_balance==10000.0 else run_one(10000.0,66,33)
                    fingerprint=result["data_quality"]["history_fingerprint"]
                    if any(x["data_quality"]["history_fingerprint"]!=fingerprint for x in (low,high)):
                        raise ValueError("Historical market data changed while comparing balances; retry on a frozen snapshot")
                    def summary(payload):
                        trades=[trade for strategy in payload["strategies"] for trade in strategy["trades"]]
                        wins=sum(trade["pnl"]>0 for trade in trades)
                        return {"balance":payload["assumptions"]["balance"],
                            "trades":len(trades),"wins":wins,
                            "win_rate":round(100*wins/len(trades),2) if trades else None,
                            "net_gain":round(sum(trade["pnl"] for trade in trades),2)}
                    result["balance_comparison"]={
                        "history_fingerprint":fingerprint,
                        "broker_assumptions_identical":True,
                        "accounts_independent":True,
                        "rows":[summary(low),summary(high)],
                    }
                else:
                    result=run_one(primary_balance,0,99)
                job["result"]=result
                job["progress_pct"]=100
                job["status"]="COMPLETED"
            except Exception as exc:
                job["status"]="FAILED"
                job["error"]=str(exc)[:500]
            job["updated_at"]=time.time()
            store(job_id,job)
