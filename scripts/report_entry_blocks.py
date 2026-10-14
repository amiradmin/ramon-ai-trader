#!/usr/bin/env python3
"""Read-only aggregate of Ramon model/EA logs. No trading or Docker changes."""
from __future__ import annotations
import argparse
from collections import Counter
from pathlib import Path
import re
import subprocess

PAT = re.compile(r"RAMON_STRUCTURE\s+setup=(\S+)\s+candidate=(\S+)\s+final=(\S+)\s+reason=(\S+)")
REASON = re.compile(r"\b(?:WAIT|BUY|SELL)\s+([a-z][a-z0-9_]+)\s+median=")
def summarize(model: str, ea: str) -> dict:
    structure = [m.groups() for line in model.splitlines() if (m := PAT.search(line))]
    # Diagnostic logs are emitted only upon state changes; not a per-decision count.
    setups = Counter(s for s, _, _, _ in structure)
    candidates = Counter(c for _, c, _, _ in structure)
    selection = Counter(r for _, _, _, r in structure)
    ea_reasons = Counter(m.group(1) for line in ea.splitlines() if (m := REASON.search(line)))
    return {"structure_state_changes": len(structure),
            "structure_setups_state_changes":dict(setups),
            "structure_candidate_state_changes":dict(candidates),
            "structure_gate_state_changes":dict(selection),
            "ea_decision_log_lines":sum(ea_reasons.values()),
            "ea_reasons":dict(ea_reasons.most_common())}

def read_mt5(path: Path) -> str:
    if not path.is_file(): return ""
    raw=path.read_bytes()
    if raw.startswith((b"\xff\xfe",b"\xfe\xff")): return raw.decode("utf-16",errors="replace")
    if b"\x00" in raw[:200]: return raw.decode("utf-16-le",errors="replace")
    return raw.decode("utf-8-sig",errors="replace")

def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--model-log",type=Path,help="Use existing model log instead of docker logs")
    ap.add_argument("--ea-log",type=Path,help="Explicit MT5 experts log file")
    ap.add_argument("--since",default="24h",help="Docker log interval")
    ap.add_argument("--container",default="ramon-ai-trader-model-1")
    args=ap.parse_args()
    if args.model_log:
        model=args.model_log.read_text(errors="replace")
    else:
        p=subprocess.run(["docker","logs","--since",args.since,args.container],
                          capture_output=True,text=True,check=False)
        if p.returncode:
            ap.error("Cannot read model logs: "+p.stderr[-300:])
        model=p.stdout+"\n"+p.stderr
    if args.ea_log:
        ea=read_mt5(args.ea_log)
    else:
        import datetime
        base=Path.home()/".mt5/drive_c/Program Files/MetaTrader 5/MQL5/logs"
        ea=read_mt5(base/(datetime.datetime.now().strftime("%Y%m%d")+".log"))
    report=summarize(model,ea)
    import json
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print("NOTE: structure counts are LOGGED STATE CHANGES, not total decisions.")
    print("NOTE: no broker fills or unique executed entries can be proven from this report.")

if __name__=="__main__":
    main()
