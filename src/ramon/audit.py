"""Read-only entry evidence and role-learning readiness, with no training or promotion."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.request import urlopen

from .core import Settings
from .ensemble import EnsembleCoordinator
from .report import load_report_trades, safe_json, trade_time, training_status
from .train_roles import (
    DEFAULT_MINIMUM_SAMPLES, DEFAULT_REGIME_MINIMUM, DEFAULT_MINIMUM_TRADES,
    read_trade_examples,
)


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))


def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {"status": "NOT_FOUND"}
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError("expected a JSON object")
        return raw
    except (OSError, ValueError) as exc:
        return {"status": "UNREADABLE", "error": str(exc)}


def csv_candidates(stream, trade: dict, sample: dict) -> list[dict]:
    """Legacy CSV has no sample key: time matches are candidates, never proof."""
    target = sample.get("quote_time") or trade["opened"]
    candidates = []
    fields = ("captured", "symbol", "decision", "reason", "base_decision", "base_reason",
              "signal_strength", "minimum_strength", "buy_edge", "sell_edge", "minimum_edge",
              "ai_trend_confirmed", "ai_trend_direction", "ai_trend_move_atr",
              "ai_trend_consistency", "intrabar_move_atr", "risk_usd",
              "min_lot_override_used", "planned_volume", "estimated_sl_units", "min_lot_sl_units")
    for row in csv.DictReader(stream):
        if row.get("symbol") != trade["symbol"]:
            continue
        try:
            # Interpret broker wall time as an integer on the same basis as quote_time.
            stamp = int(datetime.strptime(row["captured"], "%Y.%m.%d %H:%M:%S")
                        .replace(tzinfo=timezone.utc).timestamp())
        except (KeyError, ValueError, TypeError):
            continue
        if abs(stamp - target) <= 90:
            candidates.append({key: row.get(key, "UNKNOWN") for key in fields})
    return candidates


def audit(db: str, symbol: str, model: str, ensemble_dir: Path, *,
          minimum_samples: int = DEFAULT_MINIMUM_SAMPLES,
          trade_number: int | None = None, trade_key: str | None = None,
          signal_csv: str | None = None, health: dict | None = None) -> None:
    print("=== RAMON ENTRY / LEARNING AUDIT (READ ONLY) ===")
    print(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    print(f"Database: {db} | Symbol: {symbol} | Selected Chronos: {model}")
    print("=== LIVE SERVICE ===")
    print_json(health or {"status": "NOT_QUERIED"})
    print("=== ROLE BUNDLE ON DISK (may differ from the running process) ===")
    print_json(EnsembleCoordinator(ensemble_dir, model).status())
    print("=== LAST RECORDED ROLE TRAINING ATTEMPT ===")
    print_json(read_json_file(ensemble_dir / "training_report.json"))
    print("This is a historical attempt; NOT_FOUND does not prove the scheduler is disabled.")
    print("=== CURRENT TRAINER ELIGIBILITY ===")
    with sqlite3.connect(Path(db).expanduser().resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        trades = load_report_trades(con, symbol)
        print_json({"closed_trades": len(trades),
                    "exit_label_counts": dict(Counter(training_status(row) for row in trades))})
        try:
            examples = read_trade_examples(con, symbol, model)
        except (sqlite3.OperationalError, ValueError, TypeError) as exc:
            print_json({"status": "UNKNOWN", "reason": f"Trainer selection failed: {exc}"})
        else:
            print_json({"eligible_closed_trades": len(examples),
                        "positive_labels": sum(row.label == 1 for row in examples),
                        "nonpositive_labels": sum(row.label == 0 for row in examples),
                        "minimum_samples": minimum_samples,
                        "remaining_to_sample_gate": max(0, minimum_samples - len(examples)),
                        "sample_gate": "PASS" if len(examples) >= minimum_samples else "WAIT"})
        print("Sample limit is this audit's configured limit; compare with the last training attempt above.")
        print("Passing the sample gate does not activate roles. Training also requires purged base/meta/holdout windows,")
        print(f"regime history (default {DEFAULT_REGIME_MINIMUM}), >=40 samples and >=10 per class per fitted role,")
        print(f"a fresh holdout and promotion checks (default >= {DEFAULT_MINIMUM_TRADES} accepted holdout trades).")

        if trade_number is None and trade_key is None:
            return
        if trade_key is not None:
            trade = next((row for row in trades if row["trade_key"] == trade_key), None)
        else:
            trade = trades[trade_number - 1] if 1 <= trade_number <= len(trades) else None
        if trade is None:
            raise ValueError("selected trade not found; use the number in --all or its stable trade_key")
        print("=== SELECTED ENTRY ===")
        print("Number uses the full report's broker close-time order; backfills may change it. Prefer trade_key on reruns.")
        print_json({key: trade[key] for key in ("trade_key", "sample_key", "symbol", "direction",
                                               "net_units", "initial_risk_units", "net_r", "exit_reason")})
        print(f"Opened: {trade_time(trade, 'opened')} | Closed: {trade_time(trade, 'closed')}")
        sample_columns = {row[1] for row in con.execute("PRAGMA table_info(decision_samples)")}
        row = (con.execute("SELECT * FROM decision_samples WHERE sample_key=? AND symbol=?",
                           (trade["sample_key"], symbol)).fetchone()
               if {"sample_key", "symbol"} <= sample_columns else None)
        sample = dict(row) if row else {}
        print_json({key: sample.get(key, "UNKNOWN") for key in
                    ("quote_time", "chronos_model", "schema_version", "direction", "base_decision",
                     "final_decision", "stop_distance", "target_distance", "bundle_id")})
        quote = sample.get("quote_time")
        print_json({"entry_delay_broker_seconds": trade["opened"] - quote if quote is not None else "UNKNOWN"})
        print("=== STORED ENTRY FEATURES ===")
        print_json(safe_json(sample.get("entry_features")))
        metadata = safe_json(sample.get("model_metadata"))
        print("=== RECORDED DECISION AND THRESHOLDS AT ENTRY ===")
        print_json(metadata.get("decision_audit") or {
            "status": "UNKNOWN_LEGACY", "reason": "Entry reason and thresholds were not persisted in this sample."})
        print("Legacy low strength alone cannot prove an invalid entry: the continuation route has no strength floor.")
        settings = Settings()
        print(f"Current-code defaults: continuation edge >= {settings.trend_min_edge_fraction} * minimum_edge; "
              f"path >= {settings.trend_min_path_atr} ATR;")
        print(f"consistency >= {settings.trend_min_consistency}; "
              f"intrabar move >= {settings.trend_min_micro_move_atr} ATR; matching direction and micro-turn required.")
        print("These defaults are explanatory, NOT evidence of the configuration at a historical entry.")
        if signal_csv:
            print("=== LEGACY SIGNAL CSV CANDIDATES (+/-90s broker time, not an exact ID join) ===")
            if signal_csv == "-":
                candidates = csv_candidates(sys.stdin, trade, sample)
            else:
                with open(signal_csv, encoding="utf-8-sig", errors="replace", newline="") as stream:
                    candidates = csv_candidates(stream, trade, sample)
            print_json(candidates)
            print("CSV rows are decision/sizing previews; they do not prove an order was executed.")
        else:
            print("For legacy reason/sizing evidence, supply Ramon_Signals.csv; no historical reason is guessed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("RAMON_HISTORY_DB", "/data/ramon_history.sqlite3"))
    parser.add_argument("--symbol", default=os.getenv("RAMON_SYMBOL", "XAUUSD_l"))
    parser.add_argument("--ensemble-dir", type=Path, default=os.getenv("RAMON_ENSEMBLE_DIR", "/checkpoints/ensemble"))
    parser.add_argument("--chronos-model", default=None)
    parser.add_argument("--health-url", default=None)
    parser.add_argument("--minimum-samples", type=int, default=DEFAULT_MINIMUM_SAMPLES)
    parser.add_argument("--signal-csv", help="optional legacy CSV path, or - to read stdin")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--trade-number", type=int)
    choice.add_argument("--trade-key")
    args = parser.parse_args()
    if args.minimum_samples < 100:
        parser.error("minimum-samples must be >=100, matching the trainer")
    health = None
    if args.health_url:
        try:
            with urlopen(args.health_url, timeout=5) as response:
                health = json.load(response)
            if not isinstance(health, dict):
                raise ValueError("health response is not an object")
        except (OSError, ValueError) as exc:
            health = {"status": "UNAVAILABLE", "error": str(exc)}
    # The running service is authoritative; an explicit model selects another cohort.
    model = args.chronos_model or (health or {}).get("model")
    if not model:
        active = Path(os.getenv("CHRONOS_MODEL_FILE", "/checkpoints/active_model.txt"))
        model = active.read_text().strip() if active.is_file() else ""
        model = model or os.getenv("CHRONOS_MODEL", "autogluon/chronos-2-small")
        print("Live checkpoint UNKNOWN; selecting the on-disk/environment model for eligibility only.")
    audit(args.db, args.symbol, model, args.ensemble_dir,
          minimum_samples=args.minimum_samples, trade_number=args.trade_number,
          trade_key=args.trade_key, signal_csv=args.signal_csv, health=health)


if __name__ == "__main__":
    main()
