"""Isolated research-only spread-limit shadow replays.

The entry timing absolute spread limit is temporarily adjusted on the
pipeline object created *inside this laboratory process*. It does NOT modify
trading config, database thresholds, EA commands or the production worker.
All other spread/ATR, spread/risk, quality, and 0.5% sizing gates remain.
"""
from datetime import datetime, timezone
from math import isfinite
from unittest.mock import patch

from segmented import breakdown, period_metrics, partition_dates
from diagnostics import sensitivity_metric


def run_shadow(replay_module, data, rows, *, spread_points, limit_points, progress=None):
    if limit_points not in (50.0, 90.0, 130.0):
        raise ValueError("Research spread limit must be one of the fixed presets")
    if spread_points not in (42.0, 84.0, 126.0):
        raise ValueError("Research spread must match one of the frozen presets")
    if data.get("risk_percent", 0.5) > 0.5:
        raise ValueError("Risk cap may never exceed 0.5%")
    # Patch only within this laboratory Python process and this replay call.
    original=replay_module._research_pipeline

    def lab_pipeline(*args, **kwargs):
        pipeline=original(*args, **kwargs)
        configured=float(pipeline.entry_timer.maximum_spread_points)
        if not isfinite(configured) or configured <= 0:
            raise RuntimeError("Invalid loaded spread limit; shadow experiment invalid")
        # The original threshold may come from the read-only DB config table.
        # Never assume it equals the file default; do not persist this override.
        pipeline.entry_timer.maximum_spread_points=limit_points
        return pipeline

    settings=dict(data, spread_points=spread_points)
    with patch.object(replay_module, "_research_pipeline", lab_pipeline):
        outcome=replay_module._simulate(settings, rows, progress=progress)[0]
    return outcome


def key(trade):
    return (int(trade["signal_at"]), str(trade["side"]))


def cohort_difference(control, expanded, starting_balance, start, end):
    """Compare trades under IDENTICAL modeled spread/cost and changed cap only.

    Path-dependent positions can displace later entries: therefore 'newly'
    describes unique keys, not an independent additive profit attribution.
    """
    original={key(t) for t in control.get("trades", [])}
    new={key(t) for t in expanded.get("trades", [])}
    introduced=[t for t in expanded.get("trades", []) if key(t) not in original]
    displaced=[t for t in control.get("trades", []) if key(t) not in new]
    introduced_keys={key(t) for t in introduced}
    if len(introduced_keys)!=len(introduced):
        raise ValueError("Duplicate signal keys in expanded research trades")
    partitions=[]
    for span in partition_dates(start,end):
        low=span["start"]
        high=span["end"]
        selected=[t for t in introduced if low <=
            datetime.fromtimestamp(t["opened_at"], timezone.utc).date().isoformat()
            <= high]
        partitions.append({**span, **period_metrics(selected,starting_balance)})
    unaffected=[t for t in introduced if not
                (t.get("data_gap_affected") or t.get("gaps_crossed",0))]
    return {
        "newly_admitted":period_metrics(introduced,starting_balance),
        "newly_admitted_gap_unaffected":period_metrics(unaffected,starting_balance),
        "displaced_control_trades":period_metrics(displaced,starting_balance),
        "new_trades_by_entry_partition":partitions,
        "new_trade_keys_count":len(introduced_keys),
        "note":"Descriptive cohort difference under same spread; path-dependent decisions and open-position blocking can change later entries."
    }


def comparison_report(*, replay_module, data, rows, start, end, baseline,
                      quality, progress_callback=None, progress_factory=None):
    if abs(float(data["spread_points"])-42.0)>1e-8:
        raise ValueError("Frozen experiment expects a 42-point baseline spread")
    if float(data.get("slippage_points",0))!=2.0:
        raise ValueError("Frozen experiment expects 2-point baseline slippage")
    if abs(float(data.get("risk_percent",0.5))-0.5)>1e-8:
        raise ValueError("Frozen experiment requires the unchanged 0.5% risk cap")
    starting=float(data["balance"])
    # Actual configured cap is read from the standard pipeline once, without writes.
    loaded_pipeline=replay_module._research_pipeline("TREND_PULLBACK",
        risk_percent=.5, stop_buffer_atr=data.get("stop_buffer_atr"),
        target_reward_risk=data.get("target_reward_risk"),
        minimum_opportunity_score=data.get("minimum_opportunity_score"),
        minimum_entry_quality=data.get("minimum_entry_quality"))
    configured_limit=float(loaded_pipeline.entry_timer.maximum_spread_points)
    if not isfinite(configured_limit) or configured_limit<=0:
        raise ValueError("Invalid configured entry spread limit")
    runs=[("BASE_42_LIMIT_50",42.0,50.0,None),
          ("HIGH_84_LIMIT_50",84.0,50.0,None),
          ("HIGH_84_LIMIT_90",84.0,90.0,None),
          ("HIGH_126_LIMIT_50",126.0,50.0,None),
          ("HIGH_126_LIMIT_130",126.0,130.0,None)]
    details=[]
    stored={}
    for name, spread, limit, precomputed in runs:
        if progress_callback:
            progress_callback(name)
        report=precomputed if precomputed is not None else run_shadow(
            replay_module,data,rows,spread_points=spread,
            limit_points=limit,
            progress=progress_factory(name) if progress_factory else None)
        stored[name]=report
        details.append({
            "name":name,"spread_points":spread,"absolute_spread_limit_points":limit,
            "risk_percent":0.5,
            "metrics":sensitivity_metric(report),
            "gap_unaffected":period_metrics([
                t for t in report["trades"] if not
                (t.get("data_gap_affected") or t.get("gaps_crossed",0))
            ],starting),
            "periods":breakdown(report,start,end,starting,name=name)["periods"],
            "total_gap_affected_trades":sum(bool(t.get("data_gap_affected") or t.get("gaps_crossed",0))
                for t in report["trades"]),
        })
    comparisons=[]
    for spread, strict, wider in ((84,"HIGH_84_LIMIT_50","HIGH_84_LIMIT_90"),
                                   (126,"HIGH_126_LIMIT_50","HIGH_126_LIMIT_130")):
        comparisons.append({
            "modeled_spread_points":spread,
            "strict_limit":50,
            "expanded_limit":90 if spread==84 else 130,
            "cohorts":cohort_difference(stored[strict],stored[wider],starting,start,end)
        })
    return {
        "mode":"SPREAD_SHADOW_READ_ONLY_RESEARCH",
        "live_eligible":False,
        "trading_authorized":False,
        "baseline_spread_points":42,
        "configured_entry_spread_limit_points":configured_limit,
        "preflight_original_baseline":sensitivity_metric(baseline),
        "fixed_risk_percent":0.5,
        "fixed_slippage_points":2,
        "experiment":"PREDECLARED_ABSOLUTE_SPREAD_LIMITS_50_90_130",
        "scenarios":details,
        "matched_spread_control_comparisons":comparisons,
        "historical_gap_count":quality.get("gap_count"),
        "limitations":[
            "Research-only limit adjustments apply solely to in-memory strategy pipelines in an isolated process.",
            "The independently replayed 42/50 scenario is a fixed-limit control, not necessarily the currently configured production threshold; the original baseline is reported separately.",
            "All ATR-relative spread, risk-distance, signal freshness, quality, broker volume and 0.5% risk checks remain active.",
            "Bid-only M15 OHLC; spread and slippage are modeled constants, not measured historical costs.",
            "Sequential positions cause path dependence; newly admitted trades are not an independent incremental P&L estimate.",
            "Gap-affected returns are descriptive and unsafe as fill evidence.",
            "The train/validation/test split has been inspected in prior research; TEST_UNTOUCHED is no longer a pristine holdout.",
            "No live strategy promotion or risk relaxation is authorized."
        ]
    }
