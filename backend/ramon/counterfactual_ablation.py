"""Counterfactual rule ablation for Ramon realized trades.

Read-only research tool. It reuses entry_failure_audit's joined historical cohort
and asks: if a pre-entry rule had blocked a trade, what would the retained
portfolio's realized R distribution have looked like? This is not a causal
backtest and must be validated chronologically before live promotion.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

from .entry_failure_audit import load_joined_rows


def _feature(row: dict, suffix: str):
    features=row.get("features", {})
    exact=features.get(suffix)
    if exact is not None:
        return exact
    matches=[v for k,v in features.items() if k.endswith(suffix)]
    return matches[0] if matches else None


def _num(value):
    try:
        x=float(value)
    except (TypeError,ValueError):
        return None
    return x if math.isfinite(x) else None


def _truth(value) -> bool:
    x=_num(value)
    if x is not None:
        return x >= 0.5
    return str(value).strip().upper() in {"1","TRUE","YES","ON","READY"}


def metrics(rows: list[dict]) -> dict:
    wins=[r for r in rows if float(r["net_r"])>0]
    losses=[r for r in rows if float(r["net_r"])<0]
    gross_profit=sum(float(r["net_r"]) for r in wins)
    gross_loss=-sum(float(r["net_r"]) for r in losses)
    pf=(gross_profit/gross_loss) if gross_loss>0 else None
    return {
        "trades":len(rows),
        "wins":len(wins),
        "losses":len(losses),
        "win_rate":len(wins)/len(rows) if rows else None,
        "net_r":sum(float(r["net_r"]) for r in rows),
        "gross_profit_r":gross_profit,
        "gross_loss_r":gross_loss,
        "profit_factor":pf,
        "direction_weak":sum(r["labels"]["direction_weak"] for r in rows),
        "adverse_first":sum(r["labels"]["adverse_first"] for r in rows),
        "timing_stress":sum(r["labels"]["timing_stress"] for r in rows),
        "exit_left_edge":sum(r["labels"]["exit_left_edge"] for r in rows),
    }


def _policy(name: str, row: dict, *, forecast_limit: float | None=None) -> bool:
    """Return True when a historical trade would have been allowed."""
    timing=_feature(row,"entry_timing_ready")
    forecast=_num(_feature(row,"entry_features.forecast_distance_atr"))
    state=str(_feature(row,"market_assessment.state") or _feature(row,"final.market_state") or "")
    moment=str(_feature(row,"moment_anomaly_label") or "")
    base=str(_feature(row,"core.base_decision") or _feature(row,"final.base_decision") or "")
    market_direction=str(_feature(row,"final.market_direction") or "")
    side=str(row.get("direction") or "")

    if name=="baseline":
        return True
    if name=="timing_ready":
        return _truth(timing)
    if name=="base_wait_veto":
        return base != "WAIT"
    if name=="avoid_range_middle":
        return state != "RANGE_MIDDLE"
    if name=="avoid_volatility_shock":
        return state != "VOLATILITY_SHOCK"
    if name=="avoid_bad_states":
        return state not in {"RANGE_MIDDLE","VOLATILITY_SHOCK"}
    if name=="avoid_moment_elevated":
        return moment != "ELEVATED"
    if name=="buy_requires_timing_ready":
        return not (side=="BUY" and not _truth(timing))
    if name=="market_buy_requires_timing_ready":
        return not (market_direction=="BUY" and not _truth(timing))
    if name=="forecast_limit":
        return forecast is not None and forecast_limit is not None and forecast <= forecast_limit
    if name=="v3_candidate":
        if base=="WAIT":
            return False
        if timing is not None and not _truth(timing):
            return False
        if state in {"RANGE_MIDDLE","VOLATILITY_SHOCK"}:
            return False
        if moment=="ELEVATED":
            return False
        if forecast is not None and forecast_limit is not None and forecast > forecast_limit:
            return False
        return True
    raise KeyError(name)


def evaluate_policy(rows: list[dict], name: str, *, forecast_limit: float | None=None) -> dict:
    kept=[r for r in rows if _policy(name,r,forecast_limit=forecast_limit)]
    blocked=[r for r in rows if r not in kept]
    base=metrics(rows)
    km=metrics(kept)
    bm=metrics(blocked)
    return {
        "policy":name if forecast_limit is None else f"{name}_{forecast_limit:.2f}",
        "forecast_limit":forecast_limit,
        "kept":km,
        "blocked":bm,
        "delta_vs_baseline":{
            "trades":km["trades"]-base["trades"],
            "net_r":km["net_r"]-base["net_r"],
            "profit_factor":(
                km["profit_factor"]-base["profit_factor"]
                if km["profit_factor"] is not None and base["profit_factor"] is not None else None
            ),
            "win_rate":(
                km["win_rate"]-base["win_rate"]
                if km["win_rate"] is not None and base["win_rate"] is not None else None
            ),
        },
    }


def _split(rows: list[dict], fraction: float=.70):
    ordered=sorted(rows,key=lambda r:int(r.get("opened",0)))
    cut=max(1,min(len(ordered)-1,int(len(ordered)*fraction))) if len(ordered)>1 else len(ordered)
    return ordered[:cut],ordered[cut:]


def run(db: str | Path, symbol: str="XAUUSD_l") -> dict:
    path=Path(db).expanduser().resolve()
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as con:
        rows,coverage=load_joined_rows(con,symbol)

    policies=["timing_ready","base_wait_veto","avoid_range_middle","avoid_volatility_shock",
              "avoid_bad_states","avoid_moment_elevated","buy_requires_timing_ready",
              "market_buy_requires_timing_ready"]
    limits=[0.50,0.60,0.70,0.80,1.00]
    all_results=[evaluate_policy(rows,"baseline")]
    all_results += [evaluate_policy(rows,p) for p in policies]
    all_results += [evaluate_policy(rows,"forecast_limit",forecast_limit=x) for x in limits]
    all_results += [evaluate_policy(rows,"v3_candidate",forecast_limit=x) for x in limits]

    train,holdout=_split(rows)
    holdout_results=[]
    for x in limits:
        holdout_results.append({
            "forecast_limit":x,
            "train":evaluate_policy(train,"v3_candidate",forecast_limit=x),
            "holdout":evaluate_policy(holdout,"v3_candidate",forecast_limit=x),
        })

    return {
        "mode":"READ_ONLY_COUNTERFACTUAL_ABLATION",
        "symbol":symbol,
        "coverage":coverage,
        "baseline":metrics(rows),
        "policies":all_results,
        "chronological_split":{"train_n":len(train),"holdout_n":len(holdout),"train_fraction":0.70},
        "v3_candidate_holdout_grid":holdout_results,
        "guardrail":(
            "Blocked trades are evaluated using realized historical outcomes. Results are descriptive "
            "counterfactuals, not causal proof. Thresholds must survive chronological holdout/walk-forward "
            "testing before any live use."
        ),
    }


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",default="data/ramon_history.sqlite3")
    p.add_argument("--symbol",default="XAUUSD_l")
    p.add_argument("--output",default="data/counterfactual_ablation.json")
    a=p.parse_args()
    report=run(a.db,a.symbol)
    out=Path(a.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({
        "coverage":report["coverage"],
        "baseline":report["baseline"],
        "policies":report["policies"],
        "chronological_split":report["chronological_split"],
        "v3_candidate_holdout_grid":report["v3_candidate_holdout_grid"],
    },ensure_ascii=False,indent=2,allow_nan=False))


if __name__=="__main__":
    main()
