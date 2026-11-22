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
                response = run_backtest_data(data,max_candles=16000,progress=progress)
                result=json.loads(response.content)
                if response.status_code!=200:
                    raise ValueError(result.get("error","Backtest validation failed"))
                job["result"]=result
                job["progress_pct"]=100
                job["status"]="COMPLETED"
            except Exception as exc:
                job["status"]="FAILED"
                job["error"]=str(exc)[:500]
            job["updated_at"]=time.time()
            store(job_id,job)
