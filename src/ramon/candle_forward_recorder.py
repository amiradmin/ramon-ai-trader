"""Reconstructed durable forward-observation recorder (SQLite, no trading).

Only a trusted caller may supply captured predictions. A future label may be
attached later, but is never fabricated by the recorder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_predictions (
    sample_key TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    captured_utc INTEGER NOT NULL,
    feature_cutoff_utc INTEGER NOT NULL,
    horizon_bars INTEGER NOT NULL,
    probabilities_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    realized_label TEXT,
    resolved_utc INTEGER,
    CHECK (horizon_bars > 0)
)
"""


def read_candidate(context, provenance, now, source_recorded, offset=0):
    """Validate a candidate rather than silently infer its clock or history."""
    if not isinstance(now, int) or isinstance(now, bool):
        raise ValueError("now must be explicit UTC seconds")
    if not isinstance(context, dict) or not isinstance(provenance, dict):
        raise ValueError("invalid candidate")
    cutoff = context.get("feature_cutoff_utc")
    if not isinstance(cutoff, int) or isinstance(cutoff, bool):
        raise ValueError("invalid feature cutoff")
    if cutoff > now or now - cutoff > 120:
        raise ValueError("stale or future context")
    if not isinstance(source_recorded, int) or source_recorded > now:
        raise ValueError("future source record")
    probs = context.get("probabilities")
    if not isinstance(probs, (list, tuple)) or len(probs) != 3:
        raise ValueError("need DOWN/FLAT/UP probabilities")
    if any(not isinstance(p, (float, int)) or not math.isfinite(p) or p < 0 for p in probs):
        raise ValueError("invalid probabilities")
    if abs(sum(probs) - 1) > 1e-5:
        raise ValueError("probabilities must sum to 1")
    horizon = context.get("horizon_bars", 1)
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        raise ValueError("invalid horizon")
    return {"symbol": str(context.get("symbol", "XAUUSD_l")),
            "captured_utc": now, "feature_cutoff_utc": cutoff,
            "horizon_bars": horizon, "probabilities": list(probs),
            "provenance": dict(provenance)}


class Recorder:
    def __init__(self, history, output, artifact=None, model_path=None):
        self.history = Path(history) if history is not None else None
        self.output = Path(output)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.output)
        self.db.execute(SCHEMA)
        self.db.commit()

    def tick(self, candidate, now=None):
        """Store exactly one immutable prediction; repeats are ignored."""
        now = int(time.time()) if now is None else now
        sample = read_candidate(candidate, candidate.get("provenance", {}),
                                now, candidate.get("source_recorded_utc", now))
        unique = {key: sample[key] for key in
                  ("symbol", "feature_cutoff_utc", "horizon_bars", "provenance")}
        key = hashlib.sha256(json.dumps(unique, sort_keys=True).encode()).hexdigest()
        with self.db:
            cur = self.db.execute(
                """INSERT OR IGNORE INTO forward_predictions
                (sample_key,symbol,captured_utc,feature_cutoff_utc,horizon_bars,
                 probabilities_json,provenance_json)
                 VALUES (?,?,?,?,?,?,?)""",
                (key, sample["symbol"], sample["captured_utc"],
                 sample["feature_cutoff_utc"], sample["horizon_bars"],
                 json.dumps(sample["probabilities"]), json.dumps(sample["provenance"],
                 sort_keys=True)))
        return {"sample_key": key, "inserted": bool(cur.rowcount)}

    def close(self):
        self.db.close()


def summary(path):
    with sqlite3.connect(path) as con:
        total, resolved = con.execute(
            "SELECT count(*), count(realized_label) FROM forward_predictions").fetchone()
    return {"predictions": total, "resolved": resolved, "unresolved": total - resolved,
            "live_trading": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(summary(args.output), indent=2))


if __name__ == "__main__":
    main()
