from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time

from .history import ensure_history_db


def _first_hit(rows: list[tuple[int, float, float]], *, direction: str, level: float) -> tuple[int | None, int | None]:
    for index, (bar_time, high, low) in enumerate(rows, start=1):
        if direction == "BUY" and high >= level:
            return bar_time, index
        if direction == "SELL" and low <= level:
            return bar_time, index
    return None, None


def backfill_target_outcomes(db: str | Path, *, symbol: str = "XAUUSD_l") -> dict[str, int]:
    """Build closed-trade TP1/TP2/TP3 labels using only persisted market bars.

    This is observational telemetry. It does not reconstruct intrabar ordering when
    multiple levels were touched inside the same M15 candle.
    """
    # In an explicit PostgreSQL cutover configuration all reads and the
    # idempotent label write use one PostgreSQL transaction.  There is no
    # SQLite fallback: reporting a successful target-label backfill after a
    # database failure would corrupt training observability.
    if __import__("os").environ.get("RAMON_STORAGE_BACKEND", "sqlite").lower() == "postgres":
        return _backfill_target_outcomes_postgres(symbol)
    path = ensure_history_db(db)
    with sqlite3.connect(path, timeout=30) as conn:
        samples = conn.execute(
            """
            SELECT s.sample_key,t.trade_key,s.symbol,t.direction,t.opened,t.closed,
                   s.atr,s.target_structure
            FROM decision_samples s
            JOIN trade_outcomes t ON t.sample_key=s.sample_key
            LEFT JOIN target_outcomes o ON o.sample_key=s.sample_key
            WHERE s.symbol=? AND s.schema_version>=4
              AND s.target_structure IS NOT NULL
              AND t.training_status='LEARNABLE'
              AND o.sample_key IS NULL
            ORDER BY t.opened
            """,
            (symbol,),
        ).fetchall()

        inserted = skipped = 0
        for sample_key, trade_key, row_symbol, direction, opened, closed, atr, raw_target in samples:
            try:
                target = json.loads(raw_target)
                tp1, tp2, tp3 = (float(target[k]) for k in ("tp1", "tp2", "tp3"))
                if direction == "BUY" and not tp1 < tp2 < tp3:
                    raise ValueError("invalid BUY target ordering")
                if direction == "SELL" and not tp1 > tp2 > tp3:
                    raise ValueError("invalid SELL target ordering")
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                skipped += 1
                continue

            bars = conn.execute(
                """
                SELECT time,high,low FROM history_bars
                WHERE symbol=? AND timeframe='M15' AND time>=? AND time<=?
                ORDER BY time
                """,
                (row_symbol, int(opened // 900 * 900), int(closed)),
            ).fetchall()
            if not bars:
                skipped += 1
                continue

            path_rows = [(int(t), float(h), float(l)) for t, h, l in bars]
            tp1_time, bars1 = _first_hit(path_rows, direction=direction, level=tp1)
            tp2_time, bars2 = _first_hit(path_rows, direction=direction, level=tp2)
            tp3_time, bars3 = _first_hit(path_rows, direction=direction, level=tp3)
            tp1_hit, tp2_hit, tp3_hit = (int(v is not None) for v in (tp1_time, tp2_time, tp3_time))

            entry_bar = path_rows[0]
            entry_reference = (tp1 - float(target.get("impulse_end", tp1))) if direction == "BUY" else (float(target.get("impulse_end", tp1)) - tp1)
            # MFE/MAE are stored as favorable/adverse absolute price excursions from
            # the observed trade-entry sample midpoint when available.
            mid_row = conn.execute(
                "SELECT mid FROM decision_samples WHERE sample_key=?", (sample_key,)
            ).fetchone()
            reference = float(mid_row[0]) if mid_row else (entry_bar[1] + entry_bar[2]) / 2.0
            if direction == "BUY":
                mfe = max(h for _, h, _ in path_rows) - reference
                mae = reference - min(l for _, _, l in path_rows)
            else:
                mfe = reference - min(l for _, _, l in path_rows)
                mae = max(h for _, h, _ in path_rows) - reference
            mfe = max(0.0, mfe)
            mae = max(0.0, mae)

            conn.execute(
                """
                INSERT INTO target_outcomes(
                    sample_key,trade_key,symbol,direction,opened,closed,
                    tp1,tp2,tp3,tp1_hit,tp2_hit,tp3_hit,
                    tp1_time,tp2_time,tp3_time,bars_to_tp1,bars_to_tp2,bars_to_tp3,
                    mfe_price,mae_price,mfe_atr,mae_atr,
                    continuation_tp2,continuation_tp3,source,computed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sample_key, trade_key, row_symbol, direction, int(opened), int(closed),
                    tp1, tp2, tp3, tp1_hit, tp2_hit, tp3_hit,
                    tp1_time, tp2_time, tp3_time, bars1, bars2, bars3,
                    mfe, mae,
                    mfe / float(atr) if atr and float(atr) > 0 else None,
                    mae / float(atr) if atr and float(atr) > 0 else None,
                    int(tp1_hit and tp2_hit and tp2_time is not None and tp1_time is not None and tp2_time >= tp1_time),
                    int(tp2_hit and tp3_hit and tp3_time is not None and tp2_time is not None and tp3_time >= tp2_time),
                    "closed_m15_bar_path", int(time.time()),
                ),
            )
            inserted += 1

    return {"eligible": len(samples), "inserted": inserted, "skipped": skipped}


def _backfill_target_outcomes_postgres(symbol: str) -> dict[str, int]:
    """PostgreSQL equivalent of the legacy target-label contract.

    The source model stores target structure in decision metadata and outcome
    provenance in trade telemetry; this query intentionally retains the same
    eligibility, bar window, first-hit and idempotency rules as SQLite.
    """
    from .postgres_operational import DDL, _connect
    dsn = __import__("os").environ.get("RAMON_PG_DSN", "")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute("""SELECT s.sample_key,t.trade_key,s.symbol,t.direction,t.opened,t.closed,
                                  s.atr,s.metadata->'target_structure'
                           FROM ramon.decision_samples s
                           JOIN ramon.trade_outcomes t ON t.sample_key=s.sample_key
                           LEFT JOIN ramon.target_outcomes o ON o.sample_key=s.sample_key
                           WHERE s.symbol=%s AND s.schema_version>=4
                             AND s.metadata ? 'target_structure'
                             AND COALESCE(t.telemetry->>'training_status','')='LEARNABLE'
                             AND o.sample_key IS NULL
                           ORDER BY t.opened""", (symbol,))
            samples = cur.fetchall()
            inserted = skipped = 0
            for sample_key, trade_key, row_symbol, direction, opened, closed, atr, target in samples:
                try:
                    target = target if isinstance(target, dict) else json.loads(target)
                    tp1, tp2, tp3 = (float(target[k]) for k in ("tp1", "tp2", "tp3"))
                    if direction == "BUY" and not tp1 < tp2 < tp3:
                        raise ValueError("invalid BUY target ordering")
                    if direction == "SELL" and not tp1 > tp2 > tp3:
                        raise ValueError("invalid SELL target ordering")
                except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                    skipped += 1
                    continue
                cur.execute("""SELECT time,high,low FROM ramon.history_bars
                               WHERE symbol=%s AND timeframe='M15' AND time>=%s AND time<=%s
                               ORDER BY time""", (row_symbol, int(opened // 900 * 900), int(closed)))
                path_rows = [(int(t), float(h), float(l)) for t, h, l in cur.fetchall()]
                if not path_rows:
                    skipped += 1
                    continue
                tp1_time, bars1 = _first_hit(path_rows, direction=direction, level=tp1)
                tp2_time, bars2 = _first_hit(path_rows, direction=direction, level=tp2)
                tp3_time, bars3 = _first_hit(path_rows, direction=direction, level=tp3)
                tp1_hit, tp2_hit, tp3_hit = (int(v is not None) for v in (tp1_time, tp2_time, tp3_time))
                cur.execute("SELECT mid FROM ramon.decision_samples WHERE sample_key=%s", (sample_key,))
                mid_row = cur.fetchone()
                reference = float(mid_row[0]) if mid_row else (path_rows[0][1] + path_rows[0][2]) / 2.0
                if direction == "BUY":
                    mfe = max(h for _, h, _ in path_rows) - reference
                    mae = reference - min(low for _, _, low in path_rows)
                else:
                    mfe = reference - min(low for _, _, low in path_rows)
                    mae = max(h for _, h, _ in path_rows) - reference
                mfe, mae = max(0.0, mfe), max(0.0, mae)
                cur.execute("""INSERT INTO ramon.target_outcomes
                    (sample_key,trade_key,symbol,direction,opened,closed,tp1,tp2,tp3,tp1_hit,tp2_hit,tp3_hit,
                     tp1_time,tp2_time,tp3_time,bars_to_tp1,bars_to_tp2,bars_to_tp3,mfe_price,mae_price,mfe_atr,mae_atr,
                     continuation_tp2,continuation_tp3,source,computed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(sample_key) DO NOTHING""", (
                    sample_key, trade_key, row_symbol, direction, int(opened), int(closed), tp1, tp2, tp3,
                    tp1_hit, tp2_hit, tp3_hit, tp1_time, tp2_time, tp3_time, bars1, bars2, bars3, mfe, mae,
                    mfe / float(atr) if atr and float(atr) > 0 else None,
                    mae / float(atr) if atr and float(atr) > 0 else None,
                    int(tp1_hit and tp2_hit and tp2_time is not None and tp1_time is not None and tp2_time >= tp1_time),
                    int(tp2_hit and tp3_hit and tp3_time is not None and tp2_time is not None and tp3_time >= tp2_time),
                    "closed_m15_bar_path", int(time.time()),
                ))
                inserted += int(cur.rowcount == 1)
    return {"eligible": len(samples), "inserted": inserted, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill TP1/TP2/TP3 continuation outcomes")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    args = parser.parse_args()
    print(json.dumps(backfill_target_outcomes(args.db, symbol=args.symbol), sort_keys=True))


if __name__ == "__main__":
    main()
