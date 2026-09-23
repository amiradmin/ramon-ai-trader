from __future__ import annotations

import json
import sqlite3
from math import isfinite
import re
from pathlib import Path

from .core import Bar, Market


TRADE_TELEMETRY_COLUMNS = {
    "profit_units": "REAL", "commission_units": "REAL", "swap_units": "REAL", "fee_units": "REAL",
    "opened_utc_offset_seconds": "INTEGER", "closed_utc_offset_seconds": "INTEGER",
    "exit_detail": "TEXT", "entry_ea_version": "TEXT",
}

MANUAL_EXIT_REASONS = {"DEAL_REASON_CLIENT", "DEAL_REASON_MOBILE", "DEAL_REASON_WEB"}


def classify_training_status(exit_reason: str, exit_detail: str = "") -> str:
    """Classify whether a realized outcome is a valid supervised-learning label.

    Manual exits, stop-outs and legacy expert exits with an unknown trigger remain
    in account-performance reporting but are censored from role-model training.
    """
    reason = str(exit_reason or "UNKNOWN")
    detail = str(exit_detail or "")
    if reason in MANUAL_EXIT_REASONS:
        return "CENSORED_MANUAL"
    if reason == "DEAL_REASON_SO":
        return "CENSORED_STOP_OUT"
    if reason in {"DEAL_REASON_SL", "DEAL_REASON_TP"}:
        return "LEARNABLE"
    if reason == "DEAL_REASON_EXPERT":
        return "LEARNABLE" if detail else "CENSORED_AMBIGUOUS_EXPERT"
    return "CENSORED_UNKNOWN_EXIT"


HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS history_bars (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    time INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    spread_points INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, timeframe, time)
)
"""

DECISION_SAMPLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    signal_bar_time INTEGER NOT NULL,
    mid REAL NOT NULL,
    spread REAL NOT NULL,
    atr REAL NOT NULL,
    direction TEXT NOT NULL,
    base_decision TEXT NOT NULL,
    regime_features TEXT NOT NULL,
    entry_features TEXT NOT NULL,
    meta_base_features TEXT NOT NULL,
    UNIQUE(captured, symbol)
)
"""


def ensure_history_db(db: str | Path) -> Path:
    """Create Ramon's market-history and learning-sample store when needed."""
    path = Path(db).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(HISTORY_SCHEMA)
        conn.execute(DECISION_SAMPLE_SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(decision_samples)")}
        for name, definition in {
            "sample_key": "TEXT", "chronos_model": "TEXT", "schema_version": "INTEGER DEFAULT 1",
            "quote_time": "INTEGER", "stop_distance": "REAL", "target_distance": "REAL",
            "final_decision": "TEXT", "bundle_id": "TEXT", "news_features": "TEXT",
            "model_metadata": "TEXT",
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE decision_samples ADD COLUMN {name} {definition}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sample_key ON decision_samples(sample_key)")
        conn.execute("""CREATE TABLE IF NOT EXISTS trade_outcomes (
            trade_key TEXT PRIMARY KEY, sample_key TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, opened INTEGER NOT NULL, closed INTEGER NOT NULL,
            net_units REAL NOT NULL, initial_risk_units REAL NOT NULL,
            net_r REAL NOT NULL, exit_reason TEXT NOT NULL, received INTEGER NOT NULL
        )""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trade_outcomes)")}
        for name, definition in {**TRADE_TELEMETRY_COLUMNS, "training_status": "TEXT"}.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE trade_outcomes ADD COLUMN {name} {definition}")
        # Backfill deterministically from immutable broker exit provenance. Do not
        # pretend legacy DEAL_REASON_EXPERT rows have an exact trigger.
        conn.execute("""
            UPDATE trade_outcomes
            SET training_status = CASE
                WHEN exit_reason IN ('DEAL_REASON_CLIENT','DEAL_REASON_MOBILE','DEAL_REASON_WEB')
                    THEN 'CENSORED_MANUAL'
                WHEN exit_reason='DEAL_REASON_SO'
                    THEN 'CENSORED_STOP_OUT'
                WHEN exit_reason IN ('DEAL_REASON_SL','DEAL_REASON_TP')
                    THEN 'LEARNABLE'
                WHEN exit_reason='DEAL_REASON_EXPERT' AND COALESCE(exit_detail,'')<>''
                    THEN 'LEARNABLE'
                WHEN exit_reason='DEAL_REASON_EXPERT'
                    THEN 'CENSORED_AMBIGUOUS_EXPERT'
                ELSE 'CENSORED_UNKNOWN_EXIT'
            END
            WHERE training_status IS NULL OR training_status=''
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_symbol_tf_time "
            "ON history_bars(symbol,timeframe,time)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_decision_samples_symbol_time "
            "ON decision_samples(symbol,captured)"
        )
    return path


def persist_market(db: str | Path, market: Market) -> int:
    """Upsert completed bars from a live request; never store an incomplete candle."""
    path = ensure_history_db(db)
    spread_points = round((market.ask - market.bid) / market.point)
    newest = market.bars[-1].time
    rows = [
        (
            market.symbol,
            market.timeframe,
            bar.time,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            spread_points if bar.time == newest else 0,
        )
        for bar in market.bars
    ]
    with sqlite3.connect(path, timeout=10) as conn:
        conn.executemany(
            """
            INSERT INTO history_bars
                (symbol,timeframe,time,open,high,low,close,spread_points)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol,timeframe,time) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                spread_points=CASE
                    WHEN excluded.spread_points>0 THEN excluded.spread_points
                    ELSE history_bars.spread_points
                END
            """,
            rows,
        )
    return len(rows)


def persist_decision_sample(
    db: str | Path,
    *,
    captured: int,
    market: Market,
    signal_bar_time: int,
    atr: float,
    direction: str,
    base_decision: str,
    regime_features: dict[str, float],
    entry_features: dict[str, float],
    meta_base_features: dict[str, float],
    news_features: dict[str, float] | None = None,
    sample_key: str | None = None,
    chronos_model: str | None = None,
    quote_time: int | None = None,
    stop_distance: float | None = None,
    target_distance: float | None = None,
    final_decision: str | None = None,
    bundle_id: str = "",
    model_metadata: dict[str, object] | None = None,
) -> bool:
    """Persist one live inference sample for later role-model training."""
    path = ensure_history_db(db)
    mid = (market.bid + market.ask) / 2.0
    spread = market.ask - market.bid
    with sqlite3.connect(path, timeout=10) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO decision_samples
                (captured,symbol,signal_bar_time,mid,spread,atr,direction,
                 base_decision,regime_features,entry_features,meta_base_features,news_features,
                 sample_key,chronos_model,schema_version,quote_time,stop_distance,target_distance,final_decision,bundle_id,model_metadata)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(captured),
                market.symbol,
                int(signal_bar_time),
                float(mid),
                float(spread),
                float(atr),
                str(direction),
                str(base_decision),
                json.dumps(regime_features, separators=(",", ":")),
                json.dumps(entry_features, separators=(",", ":")),
                json.dumps(meta_base_features, separators=(",", ":")),
                json.dumps(news_features, separators=(",", ":")) if news_features is not None else None,
                sample_key, chronos_model, 3 if sample_key and quote_time and news_features is not None else (2 if sample_key and quote_time else 1), quote_time,
                stop_distance, target_distance, final_decision, bundle_id,
                json.dumps(model_metadata, allow_nan=False) if model_metadata is not None else None,
            ),
        )
        return cursor.rowcount == 1


def decision_sample_count(db: str | Path, symbol: str = "XAUUSD_l") -> int:
    path = Path(db)
    if not path.is_file():
        return 0
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM decision_samples WHERE symbol=?",
            (symbol,),
        ).fetchone()
    return int(row[0] if row else 0)


def history_count(db: str | Path, symbol: str = "XAUUSD_l") -> int:
    path = Path(db)
    if not path.is_file():
        return 0
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM history_bars WHERE symbol=? AND timeframe='M15'",
            (symbol,),
        ).fetchone()
    return int(row[0] if row else 0)


def load_bars(db: str | Path, symbol: str = "XAUUSD_l") -> tuple[tuple[Bar, ...], tuple[int, ...]]:
    """Read Ramon's or the existing bridge's historical M15 schema."""
    if not Path(db).is_file():
        raise FileNotFoundError(db)
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            """SELECT time,open,high,low,close,COALESCE(spread_points,0)
               FROM history_bars WHERE symbol=? AND timeframe='M15'
               ORDER BY time""",
            (symbol,),
        ).fetchall()
    bars = tuple(Bar(int(t), float(o), float(h), float(l), float(c)) for t, o, h, l, c, _ in rows)
    spreads = tuple(int(value) for *_, value in rows)
    if not bars:
        raise ValueError(f"no {symbol}/M15 history in {db}")
    return bars, spreads


def persist_trade_outcome(db: str | Path, payload: dict[str, object], received: int) -> None:
    """Idempotently reconcile a fully closed MT5 position, including all deal costs.

    The sample join is checked again when training; out-of-order delivery is allowed.
    Raw account-unit profit and risk use the same denomination, so R is cent-safe.
    """
    sample_key = str(payload["sample_key"])
    if not re.fullmatch(r"[a-f0-9]{16}", sample_key):
        raise ValueError("invalid sample key")
    trade_key, symbol, direction = (str(payload[k]) for k in ("trade_key", "symbol", "direction"))
    opened, closed = int(payload["opened"]), int(payload["closed"])
    net, risk = float(payload["net_units"]), float(payload["initial_risk_units"])
    if (not trade_key or len(trade_key) > 256 or not symbol.startswith("XAUUSD")
            or direction not in {"BUY", "SELL"} or not 0 < opened <= closed
            or not isfinite(net) or not isfinite(risk) or risk <= 0 or not isfinite(net / risk)):
        raise ValueError("invalid closed trade outcome")
    extra = validate_trade_telemetry(payload, net)
    exit_reason = str(payload.get("exit_reason", "UNKNOWN"))
    extra["training_status"] = classify_training_status(
        exit_reason, str(extra.get("exit_detail", "") or "")
    )
    extra_names = [*TRADE_TELEMETRY_COLUMNS, "training_status"]
    updates = []
    for name in extra_names:
        if name in {"profit_units", "commission_units", "swap_units", "fee_units"}:
            # A later legacy correction may change net without resending components.
            # Invalidate the stale breakdown instead of silently reporting inconsistent costs.
            updates.append(f"{name}=CASE WHEN excluded.{name} IS NOT NULL THEN excluded.{name} "
                           f"WHEN ABS(excluded.net_units-trade_outcomes.net_units)>0.0001 THEN NULL "
                           f"ELSE trade_outcomes.{name} END")
        elif name in {"exit_detail", "closed_utc_offset_seconds"}:
            updates.append(f"{name}=CASE WHEN excluded.{name} IS NOT NULL THEN excluded.{name} "
                           f"WHEN excluded.closed!=trade_outcomes.closed OR "
                           f"excluded.exit_reason!=trade_outcomes.exit_reason THEN NULL "
                           f"ELSE trade_outcomes.{name} END")
        elif name == "training_status":
            updates.append(
                "training_status=CASE "
                "WHEN excluded.exit_reason=trade_outcomes.exit_reason "
                "AND excluded.exit_reason='DEAL_REASON_EXPERT' "
                "AND excluded.exit_detail IS NULL "
                "AND trade_outcomes.exit_detail IS NOT NULL "
                "THEN trade_outcomes.training_status "
                "ELSE excluded.training_status END"
            )
        else:
            updates.append(f"{name}=COALESCE(excluded.{name},trade_outcomes.{name})")
    extra_updates = ", ".join(updates)
    path = ensure_history_db(db)
    with sqlite3.connect(path, timeout=10) as conn:
        conn.execute(f"""INSERT INTO trade_outcomes
            (trade_key,sample_key,symbol,direction,opened,closed,net_units,initial_risk_units,
             net_r,exit_reason,received,{",".join(extra_names)})
            VALUES (?,?,?,?,?,?,?,?,?,?,?,{",".join("?" for _ in extra_names)})
            ON CONFLICT(trade_key) DO UPDATE SET
                net_units=excluded.net_units, initial_risk_units=excluded.initial_risk_units,
                net_r=excluded.net_r, closed=excluded.closed, exit_reason=excluded.exit_reason,
                received=excluded.received, {extra_updates}
            WHERE trade_outcomes.sample_key=excluded.sample_key
              AND trade_outcomes.symbol=excluded.symbol
              AND trade_outcomes.direction=excluded.direction
              AND trade_outcomes.opened=excluded.opened""",
            (trade_key, sample_key, symbol, direction, opened, closed, net, risk,
             net / risk, exit_reason, int(received),
             *(extra.get(name) for name in extra_names)))


def validate_trade_telemetry(payload: dict[str, object], net: float) -> dict[str, object]:
    """Keep missing legacy telemetry NULL; reject nonfinite or unreconciled costs."""
    extra: dict[str, object] = {}
    cost_names = ("profit_units", "commission_units", "swap_units", "fee_units")
    present = [name for name in cost_names if payload.get(name) is not None]
    if present:
        if len(present) != len(cost_names):
            raise ValueError("cost breakdown must include all four components")
        for name in cost_names:
            value = float(payload[name])
            if not isfinite(value):
                raise ValueError("invalid cost breakdown")
            extra[name] = value
        if abs(sum(float(extra[name]) for name in cost_names) - net) > 0.0001:
            raise ValueError("cost breakdown does not reconcile to net_units")
    for name in ("opened_utc_offset_seconds", "closed_utc_offset_seconds"):
        if payload.get(name) is not None:
            value = float(payload[name])
            if not isfinite(value) or not value.is_integer() or abs(value) > 14 * 3600:
                raise ValueError("invalid broker UTC offset")
            extra[name] = int(value)
    for name in ("exit_detail", "entry_ea_version"):
        if payload.get(name):
            value = str(payload[name])
            if len(value) > 128 or any(ord(c) < 32 for c in value):
                raise ValueError("invalid trade telemetry text")
            extra[name] = value
    return extra
