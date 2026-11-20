"""Descriptive per-setup and per-session R summaries. No execution gating."""
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean
from zoneinfo import ZoneInfo
import math
import random
from .research_evidence import assess_evidence

LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")


def trading_session(epoch):
    """Labels overlapping London / NY hours explicitly, in DST-aware local times."""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    london = 8 <= dt.astimezone(LONDON).hour < 17
    new_york = 8 <= dt.astimezone(NEW_YORK).hour < 17
    if london and new_york:
        return "LONDON_NEW_YORK_OVERLAP"
    if london:
        return "LONDON"
    if new_york:
        return "NEW_YORK"
    return "OTHER"


def expectancy_summary(trades, *, seed=20261119, bootstrap_samples=2000):
    """Bootstrap is descriptive only: serial correlation is not modeled."""
    values = [float(item["net_r"]) for item in trades]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("nonfinite trade R")
    n = len(values)
    if not n:
        return {"trades": 0, "expectancy_r": None, "ci95_r": None,
                "ci_method": "UNAVAILABLE_NO_TRADES", "profit_factor_r": None}
    positives = sum(max(0, r) for r in values)
    negatives = -sum(min(0, r) for r in values)
    result = {
        "trades": n, "expectancy_r": round(mean(values), 6),
        "total_r": round(sum(values), 6),
        "win_rate": round(sum(x > 0 for x in values) / n, 6),
        "profit_factor_r": round(positives / negatives, 6) if negatives else None,
        "ci95_r": None, "ci_method": "INSUFFICIENT_TRADES_LT_30",
    }
    if n >= 30:
        rng = random.Random(seed)
        estimates = sorted(
            sum(values[rng.randrange(n)] for _ in range(n)) / n
            for _ in range(bootstrap_samples)
        )
        result["ci95_r"] = [round(estimates[int(.025 * bootstrap_samples)], 6),
                            round(estimates[int(.975 * bootstrap_samples) - 1], 6)]
        result["ci_method"] = "IID_BOOTSTRAP_DESCRIPTIVE_95_NOT_SELECTION_ADJUSTED"
    return result


def grouped_summary(trades):
    setups, sessions = defaultdict(list), defaultdict(list)
    for trade in trades:
        setups[trade["setup"]].append(trade)
        sessions[trading_session(trade["entry_time"])].append(trade)
    setup_summaries = {name: expectancy_summary(rows) for name, rows in sorted(setups.items())}
    return {
        "all": expectancy_summary(trades),
        "setups": setup_summaries,
        "setup_evidence": {name: assess_evidence(trades=summary["trades"], interval=summary["ci95_r"])
                           for name, summary in setup_summaries.items()},
        "sessions": {name: expectancy_summary(rows) for name, rows in sorted(sessions.items())},
    }
