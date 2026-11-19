"""Pre-registered, descriptive SELL-entry research. No trading permissions or orders.

Historical filter replay re-scores already-filled trades; skipped trades have no
known counterfactual execution, so the output is not a deployable backtest.
"""
from collections import defaultdict

def _finite(value):
    from math import isfinite
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if isfinite(number) else None


def _stats(rows):
    n = len(rows)
    wins = sum(r["pnl"] > 0 for r in rows)
    losses = sum(r["pnl"] < 0 for r in rows)
    profit = sum(max(0, r["pnl"]) for r in rows)
    loss = sum(max(0, -r["pnl"]) for r in rows)
    return {"trades": n, "wins": wins, "losses": losses,
            "win_rate": round(100 * wins / n, 2) if n else None,
            "net_pnl": round(sum(r["pnl"] for r in rows), 2),
            "profit_factor": round(profit / loss, 3) if loss else None}


def _classification(row):
    """Evidence-backed flags, not asserted trade mistakes."""
    if row["side"] != "SELL":
        return ["NOT_SELL"]
    if row["evidence_status"] != "VERIFIED_EVALUATION":
        return ["ENTRY_EVIDENCE_UNVERIFIED"]
    flags = []
    if row["regime"] == "BULLISH":
        flags.append("SELL_AGAINST_BULLISH_REGIME")
    if row["ema_bias"] == "BULL":
        flags.append("SELL_AGAINST_BULLISH_EMA")
    if row["regime"] in ("RANGING", "UNCERTAIN"):
        flags.append("NON_BEARISH_REGIME")
    chase = _finite(row.get("chase_atr"))
    if chase is not None and chase > 0.35:
        flags.append("EXTENDED_ENTRY_OVER_035_ATR")
    if chase is None:
        flags.append("CHASE_EVIDENCE_MISSING")
    if not flags:
        flags.append("NO_PREDEFINED_FLAG")
    return flags


# Fixed research hypotheses; thresholds are not selected by inspecting the holdout.
_VARIANTS = {
    "baseline": "All verified SELL outcomes",
    "bearish_regime": "Require recorded BEARISH regime",
    "bearish_regime_ema": "Require BEARISH regime and BEAR EMA bias",
    "bearish_regime_ema_no_chase": "Require BEARISH regime, BEAR EMA and chase <= 0.35 ATR",
}


def _eligible(name, row):
    if name == "baseline":
        return True
    if row["regime"] != "BEARISH":
        return False
    if name == "bearish_regime":
        return True
    if row["ema_bias"] != "BEAR":
        return False
    if name == "bearish_regime_ema":
        return True
    chase = _finite(row.get("chase_atr"))
    return chase is not None and chase <= 0.35


def analyze_sell_research(rows, *, truncated=False):
    """Chronological 70/30 temporal split over verified, completed SELL trades.

    Costs are assumed to be included in realized broker P/L (validate ingestion).
    The replay does not fill, replace or simulate orders and is subject to
    selection/survivorship bias: it cannot value missed trades.
    """
    sells = [dict(row, evidence_flags=_classification(row)) for row in rows if row["side"] == "SELL"]
    sells.sort(key=lambda row: (row["closed_at"], row["trade_id"]))
    verified = [r for r in sells if r["evidence_status"] == "VERIFIED_EVALUATION"]
    missing = len(sells) - len(verified)
    summary = {"all_sell": _stats(sells),
               "sell_winners": _stats([r for r in sells if r["pnl"] > 0]),
               "sell_losers": _stats([r for r in sells if r["pnl"] < 0]),
               "verified_sell": _stats(verified),
               "unverified_sell_count": missing,
               "truncated": truncated}
    grouped = defaultdict(list)
    for row in sells:
        for flag in row["evidence_flags"]:
            grouped[flag].append(row)
    flags = [{"flag": key, **_stats(group)} for key, group in sorted(grouped.items())]
    # Splitting by closure time is descriptive: trades can have overlapping holding
    # periods and must be purged before any model training or genuine backtest.
    n = len(verified)
    cut = int(n * 0.7)
    splits = [("earlier_70pct", verified[:cut]), ("later_30pct", verified[cut:])]
    variants = []
    for key, description in _VARIANTS.items():
        split_results = []
        for label, group in splits:
            retained = [r for r in group if _eligible(key, r)]
            split_results.append({"period": label, "eligible": len(group),
                                  "retained": len(retained), "skipped": len(group) - len(retained),
                                  "results": _stats(retained)})
        variants.append({"key": key, "description": description, "splits": split_results})
    return {
        "summary": summary, "evidence_flags": flags, "variants": variants,
        "research_ready": n >= 30 and not truncated,
        "verified_sample_size": n, "split_at": cut,
        "method": "HISTORICAL_FILLED_TRADE_FILTER_REPLAY",
        "limitations": [
            "Not a full market replay or executable backtest: no new entries or missed-trade outcomes are simulated.",
            "Variants filter already-executed trades, so apparent improvements are subject to selection bias.",
            "Chronological split uses closed-at timestamps; overlapping trades are not purged.",
            "Small or truncated samples are exploratory. Do not tune thresholds on the later split.",
            "No strategy, risk, training or command settings are modified.",
        ],
    }
