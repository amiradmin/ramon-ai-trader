from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import time

HORIZONS_SECONDS = (60, 180, 300, 900, 1800)
DEFAULT_MIN_MOVE_ATR = 0.10

SIGNAL_OUTCOME_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_outcomes (
    sample_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    quote_time INTEGER NOT NULL,
    reference_mid REAL NOT NULL,
    atr REAL NOT NULL,
    candidate_direction TEXT NOT NULL,
    final_decision TEXT NOT NULL,
    block_reason TEXT NOT NULL,
    realized_close REAL NOT NULL,
    move_atr REAL NOT NULL,
    realized_direction TEXT NOT NULL,
    direction_correct INTEGER,
    forecast_direction TEXT NOT NULL,
    forecast_correct INTEGER,
    ai_trend_direction TEXT NOT NULL,
    ai_trend_confirmed INTEGER NOT NULL,
    ai_trend_correct INTEGER,
    intrabar_direction TEXT NOT NULL,
    intrabar_confirmed INTEGER NOT NULL,
    intrabar_correct INTEGER,
    strength_pass INTEGER NOT NULL,
    edge_pass INTEGER NOT NULL,
    was_blocked INTEGER NOT NULL,
    false_block INTEGER NOT NULL,
    computed_at INTEGER NOT NULL,
    PRIMARY KEY (sample_key, horizon_seconds)
)
"""


def ensure_signal_outcome_schema(conn: sqlite3.Connection) -> None:
    conn.execute(SIGNAL_OUTCOME_SCHEMA)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_outcomes_symbol_horizon "
        "ON signal_outcomes(symbol,horizon_seconds,quote_time)"
    )


def _safe_json(raw: object) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _direction_from_move(move_atr: float, minimum: float) -> str:
    if move_atr >= minimum:
        return "BUY"
    if move_atr <= -minimum:
        return "SELL"
    return "FLAT"


def _correct(direction: str, realized_direction: str) -> int | None:
    if direction not in {"BUY", "SELL"} or realized_direction == "FLAT":
        return None
    return int(direction == realized_direction)


def _future_close(
    conn: sqlite3.Connection, *, symbol: str, quote_time: int, horizon_seconds: int
) -> float | None:
    if horizon_seconds <= 300:
        timeframe, seconds = "M1", 60
    else:
        timeframe, seconds = "M15", 900
    target = quote_time + horizon_seconds
    row = conn.execute(
        """
        SELECT close FROM history_bars
        WHERE symbol=? AND timeframe=? AND time>=?
        ORDER BY time ASC LIMIT 1
        """,
        (symbol, timeframe, target - seconds),
    ).fetchone()
    return float(row[0]) if row is not None else None


def _sample_features(row: sqlite3.Row) -> dict[str, object]:
    metadata = _safe_json(row["model_metadata"])
    audit = metadata.get("decision_audit") if isinstance(metadata, dict) else None
    audit = audit if isinstance(audit, dict) else {}
    base = audit.get("base") if isinstance(audit.get("base"), dict) else {}
    final = audit.get("final") if isinstance(audit.get("final"), dict) else {}

    candidate = str(row["direction"] or "NONE")
    final_decision = str(row["final_decision"] or row["base_decision"] or "WAIT")
    reason = str(final.get("reason") or base.get("reason") or "UNKNOWN")
    mid = float(row["mid"])

    try:
        forecast_median = float(base.get("forecast_median", mid))
    except (TypeError, ValueError):
        forecast_median = mid
    forecast_direction = (
        "BUY" if forecast_median > mid else "SELL" if forecast_median < mid else "NONE"
    )

    def flag(name: str) -> int:
        try:
            return int(float(base.get(name, 0)) >= 0.5)
        except (TypeError, ValueError):
            return 0

    def text(name: str) -> str:
        value = str(base.get(name, "NONE"))
        return value if value in {"BUY", "SELL", "NONE", "WAIT"} else "NONE"

    try:
        strength_pass = int(
            float(base.get("signal_strength", 0.0))
            >= float(base.get("minimum_strength", 1e99))
        )
    except (TypeError, ValueError):
        strength_pass = 0
    try:
        edge = max(float(base.get("buy_edge", 0.0)), float(base.get("sell_edge", 0.0)))
        edge_pass = int(edge >= float(base.get("minimum_edge", 1e99)))
    except (TypeError, ValueError):
        edge_pass = 0

    return {
        "candidate_direction": candidate,
        "final_decision": final_decision,
        "block_reason": reason,
        "forecast_direction": forecast_direction,
        "ai_trend_direction": text("ai_trend_direction"),
        "ai_trend_confirmed": flag("ai_trend_confirmed"),
        "intrabar_direction": text("intrabar_direction"),
        "intrabar_confirmed": flag("intrabar_confirmed"),
        "strength_pass": strength_pass,
        "edge_pass": edge_pass,
    }


def backfill_signal_outcomes(
    db: str | Path,
    *,
    symbol: str = "XAUUSD_l",
    horizons: tuple[int, ...] = HORIZONS_SECONDS,
    minimum_move_atr: float = DEFAULT_MIN_MOVE_ATR,
    limit: int = 2000,
    now: int | None = None,
) -> int:
    """Score stored decisions after future prices mature. This never changes execution."""
    if minimum_move_atr <= 0:
        raise ValueError("minimum_move_atr must be positive")
    if any(h <= 0 for h in horizons):
        raise ValueError("horizons must be positive")
    path = Path(db).expanduser().resolve()
    if not path.exists():
        return 0
    computed = int(time.time()) if now is None else int(now)
    inserted = 0
    with sqlite3.connect(path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        ensure_signal_outcome_schema(conn)
        rows = conn.execute(
            """
            SELECT sample_key,symbol,quote_time,captured,mid,atr,direction,base_decision,
                   final_decision,model_metadata
            FROM decision_samples
            WHERE symbol=? AND sample_key IS NOT NULL AND sample_key<>''
              AND atr>0 AND mid>0
            ORDER BY COALESCE(quote_time,captured) DESC
            LIMIT ?
            """,
            (symbol, int(limit)),
        ).fetchall()
        for row in rows:
            quote_time = int(row["quote_time"] or row["captured"])
            features = _sample_features(row)
            for horizon in horizons:
                if conn.execute(
                    "SELECT 1 FROM signal_outcomes WHERE sample_key=? AND horizon_seconds=?",
                    (row["sample_key"], int(horizon)),
                ).fetchone():
                    continue
                realized = _future_close(
                    conn, symbol=symbol, quote_time=quote_time, horizon_seconds=int(horizon)
                )
                if realized is None:
                    continue
                mid, atr = float(row["mid"]), float(row["atr"])
                move_atr = (realized - mid) / atr
                realized_direction = _direction_from_move(move_atr, minimum_move_atr)
                candidate = str(features["candidate_direction"])
                final_decision = str(features["final_decision"])
                was_blocked = int(final_decision == "WAIT" and candidate in {"BUY", "SELL"})
                candidate_correct = _correct(candidate, realized_direction)
                false_block = int(was_blocked and candidate_correct == 1)
                forecast_direction = str(features["forecast_direction"])
                ai_direction = str(features["ai_trend_direction"])
                intrabar_direction = str(features["intrabar_direction"])
                conn.execute(
                    """
                    INSERT INTO signal_outcomes (
                        sample_key,symbol,horizon_seconds,quote_time,reference_mid,atr,
                        candidate_direction,final_decision,block_reason,realized_close,move_atr,
                        realized_direction,direction_correct,forecast_direction,forecast_correct,
                        ai_trend_direction,ai_trend_confirmed,ai_trend_correct,
                        intrabar_direction,intrabar_confirmed,intrabar_correct,
                        strength_pass,edge_pass,was_blocked,false_block,computed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["sample_key"], symbol, int(horizon), quote_time, mid, atr,
                        candidate, final_decision, str(features["block_reason"]), realized,
                        move_atr, realized_direction, candidate_correct, forecast_direction,
                        _correct(forecast_direction, realized_direction), ai_direction,
                        int(features["ai_trend_confirmed"]),
                        _correct(ai_direction, realized_direction), intrabar_direction,
                        int(features["intrabar_confirmed"]),
                        _correct(intrabar_direction, realized_direction),
                        int(features["strength_pass"]), int(features["edge_pass"]),
                        was_blocked, false_block, computed,
                    ),
                )
                inserted += 1
    return inserted


def _rate(rows: list[sqlite3.Row], field: str) -> dict[str, object]:
    values = [int(row[field]) for row in rows if row[field] is not None]
    return {
        "samples": len(values),
        "correct": sum(values),
        "accuracy": (sum(values) / len(values)) if values else None,
    }


def build_signal_outcome_report(
    db: str | Path, *, symbol: str = "XAUUSD_l", horizon_seconds: int = 300
) -> dict[str, object]:
    path = Path(db).expanduser().resolve()
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_signal_outcome_schema(conn)
        rows = conn.execute(
            "SELECT * FROM signal_outcomes WHERE symbol=? AND horizon_seconds=? ORDER BY quote_time",
            (symbol, int(horizon_seconds)),
        ).fetchall()
    blocked = [row for row in rows if int(row["was_blocked"]) == 1]
    false_blocks = [row for row in rows if int(row["false_block"]) == 1]
    reasons = Counter(str(row["block_reason"]) for row in false_blocks)
    return {
        "symbol": symbol,
        "horizon_seconds": int(horizon_seconds),
        "samples": len(rows),
        "direction": _rate(rows, "direction_correct"),
        "forecast": _rate(rows, "forecast_correct"),
        "ai_trend": _rate(rows, "ai_trend_correct"),
        "intrabar": _rate(rows, "intrabar_correct"),
        "blocked": len(blocked),
        "false_blocks": len(false_blocks),
        "false_block_rate": (len(false_blocks) / len(blocked)) if blocked else None,
        "false_block_reasons": dict(reasons.most_common()),
        "false_block_components": {
            "strength_failed": sum(1 for row in false_blocks if not int(row["strength_pass"])),
            "intrabar_unconfirmed": sum(
                1 for row in false_blocks if not int(row["intrabar_confirmed"])
            ),
            "ai_trend_unconfirmed": sum(
                1 for row in false_blocks if not int(row["ai_trend_confirmed"])
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure Ramon direction accuracy and false-block filters"
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--horizon", type=int, default=300, choices=HORIZONS_SECONDS)
    parser.add_argument("--minimum-move-atr", type=float, default=DEFAULT_MIN_MOVE_ATR)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.backfill:
        backfill_signal_outcomes(
            args.db, symbol=args.symbol, minimum_move_atr=args.minimum_move_atr
        )
    report = build_signal_outcome_report(
        args.db, symbol=args.symbol, horizon_seconds=args.horizon
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(f"=== RAMON SIGNAL OUTCOME LEARNING ({args.symbol}) ===")
    print(f"Horizon: {args.horizon}s | samples={report['samples']} | blocked={report['blocked']}")
    print(f"Direction: {report['direction']}")
    print(f"Forecast: {report['forecast']}")
    print(f"AI trend: {report['ai_trend']}")
    print(f"Intrabar: {report['intrabar']}")
    print(f"False blocks: {report['false_blocks']} | rate={report['false_block_rate']}")
    print(f"Reasons: {report['false_block_reasons']}")
    print(f"Components: {report['false_block_components']}")


if __name__ == "__main__":
    main()
