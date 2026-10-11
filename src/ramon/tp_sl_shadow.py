"""Offline first-touch TP/SL labeling using chronological executable bid/ask quotes.

No training, live model wiring or trading side effects. Quotes are observations,
not fills; gaps/latency and broker execution can change realized outcomes.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Quote:
    time_ms: int
    bid: float
    ask: float

    def __post_init__(self):
        if (self.time_ms <= 0 or not math.isfinite(self.bid)
                or not math.isfinite(self.ask) or not 0 < self.bid < self.ask):
            raise ValueError("invalid executable quote")


@dataclass(frozen=True)
class PathRequest:
    direction: str
    entry_ms: int
    entry_price: float
    stop: float
    target: float
    horizon_ms: int
    max_gap_ms: int = 60_000

    def __post_init__(self):
        if self.direction not in ("BUY", "SELL"):
            raise ValueError("direction must be BUY or SELL")
        if self.entry_ms <= 0 or self.horizon_ms <= 0 or self.max_gap_ms <= 0:
            raise ValueError("invalid time window")
        if not all(math.isfinite(v) and v > 0 for v in (self.entry_price, self.stop, self.target)):
            raise ValueError("invalid price")
        if not (self.stop < self.entry_price < self.target if self.direction == "BUY"
                else self.target < self.entry_price < self.stop):
            raise ValueError("TP/SL must straddle entry")


def first_touch(req: PathRequest, quotes: list[Quote]) -> dict:
    """Report first observed executable side touching TP or SL.

    UNKNOWN when quote coverage is absent or gaps prevent a defensible label.
    Never use a quote after horizon; no synthetic candles or fills.
    """
    if any(b.time_ms <= a.time_ms for a, b in zip(quotes, quotes[1:])):
        raise ValueError("quotes must have strictly increasing timestamps")
    end = req.entry_ms + req.horizon_ms
    used = [q for q in quotes if req.entry_ms <= q.time_ms <= end]
    if not used or used[0].time_ms - req.entry_ms > req.max_gap_ms:
        return {"label": "UNKNOWN", "reason": "missing_entry_coverage", "touch_ms": None}
    prev = req.entry_ms
    for quote in used:
        if quote.time_ms - prev > req.max_gap_ms:
            return {"label": "UNKNOWN", "reason": "quote_gap", "touch_ms": None}
        prev = quote.time_ms
        # BUY closes on BID, SELL closes on ASK.
        close_side = quote.bid if req.direction == "BUY" else quote.ask
        sl = close_side <= req.stop if req.direction == "BUY" else close_side >= req.stop
        tp = close_side >= req.target if req.direction == "BUY" else close_side <= req.target
        if sl or tp:
            return {"label": "SL" if sl else "TP", "reason": "observed_first_touch",
                    "touch_ms": quote.time_ms}
    if end - used[-1].time_ms > req.max_gap_ms:
        return {"label": "UNKNOWN", "reason": "missing_horizon_coverage", "touch_ms": None}
    return {"label": "TIMEOUT", "reason": "no_observed_touch", "touch_ms": None}


def calibration_report(rows: list[dict], *, min_samples: int = 30) -> dict:
    """Evaluate *provided* pre-registered TP probabilities on TP/SL-resolved paths.

    UNKNOWN and TIMEOUT are censored, not counted as failures.
    A probability is never inferred from a direction score or price forecast.
    """
    if min_samples < 1:
        raise ValueError("invalid sample threshold")
    seen = set()
    n = 0
    brier = 0.0
    abs_error = 0.0
    counts = Counter()
    bins = {i: [0, 0.0, 0.0] for i in range(10)}
    for row in rows:
        key = str(row.get("opportunity_id", "")).strip()
        if not key or key in seen:
            raise ValueError("missing or duplicate opportunity_id")
        seen.add(key)
        label = str(row.get("label", "")).upper()
        if label not in ("TP", "SL", "UNKNOWN", "TIMEOUT"):
            raise ValueError("invalid path label")
        counts[label] += 1
        raw = row.get("p_tp_first")
        if raw is None or str(raw).strip() == "":
            if label in ("TP", "SL"):
                raise ValueError("missing precomputed p_tp_first for resolved path")
            continue
        p = float(raw)
        if not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("invalid probability")
        if label not in ("TP", "SL"):
            continue
        y = 1.0 if label == "TP" else 0.0
        n += 1
        brier += (p - y) ** 2
        abs_error += abs(p - y)
        bucket = min(int(p * 10), 9)
        bins[bucket][0] += 1
        bins[bucket][1] += p
        bins[bucket][2] += y
    return {
        "mode": "OFFLINE_CALIBRATION_ONLY",
        "total": len(rows),
        "resolved": n,
        "labels": dict(sorted(counts.items())),
        "brier_score": round(brier / n, 8) if n else None,
        "mean_absolute_error": round(abs_error / n, 8) if n else None,
        "minimum_samples_met": n >= min_samples,
        "bins": [
            {"range": f"{i/10:.1f}-{(i+1)/10:.1f}", "n": v[0],
             "mean_predicted": round(v[1]/v[0], 6) if v[0] else None,
             "observed_tp_rate": round(v[2]/v[0], 6) if v[0] else None}
            for i, v in bins.items()
        ],
        "limitations": [
            "Resolved-only scoring can be biased when timeout/censoring depends on market state.",
            "Probabilities must be generated out of sample using information available at decision time.",
            "Quote first touch is not a guaranteed executable fill, and bid/ask gaps affect accuracy.",
            "Minimum sample count alone does not establish calibration or positive net expectancy.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scored_paths_csv", type=Path)
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.scored_paths_csv.open(encoding="utf-8-sig", newline="") as stream:
        report = calibration_report(list(csv.DictReader(stream)), min_samples=args.min_samples)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
