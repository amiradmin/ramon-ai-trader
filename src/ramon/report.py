from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path
import argparse

from .history import classify_training_status


def pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else 0.0


def fmt_time(value: int | None) -> str:
    if not value:
        return "-"
    return datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%d %H:%M")


def safe_json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        raw = json.loads(value)
        return raw if isinstance(raw, dict) else {}
    except (ValueError, TypeError):
        return {}


def mean(values: list[float]) -> float:
    return statistics.mean(values) if values else float("nan")


def pooled_effect(a: list[float], b: list[float]) -> float:
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    sa = statistics.stdev(a)
    sb = statistics.stdev(b)
    pooled = math.sqrt(
        ((len(a) - 1) * sa * sa + (len(b) - 1) * sb * sb)
        / (len(a) + len(b) - 2)
    )
    if pooled < 1e-12:
        return 0.0
    return (mean(a) - mean(b)) / pooled



def trade_time(row: dict, field: str) -> str:
    """Render recorded UTC, never relabel unconverted broker wall time as UTC."""
    offset = row.get(field + "_utc_offset_seconds")
    if offset is None:
        return fmt_time(row[field]) + " BROKER[UTC offset unknown]"
    return fmt_time(row[field] - offset) + " UTC"


def load_report_trades(con: sqlite3.Connection, symbol: str) -> list[dict]:
    """Read old and new schemas without migrating or modifying the live database."""
    samples = {row[1] for row in con.execute("PRAGMA table_info(decision_samples)")}
    names = ("chronos_model", "bundle_id", "model_metadata", "captured")
    if "sample_key" in samples:
        fields = ",".join(f"s.{n} AS {n}" if n in samples else f"NULL AS {n}" for n in names)
        query = f"SELECT t.*, {fields} FROM trade_outcomes t LEFT JOIN decision_samples s ON s.sample_key=t.sample_key AND s.symbol=t.symbol WHERE t.symbol=? ORDER BY t.closed,t.trade_key"
    else:
        query = "SELECT * FROM trade_outcomes WHERE symbol=? ORDER BY closed,trade_key"
    return [dict(row) for row in con.execute(query, (symbol,))]


def bundle_label(row: dict) -> str:
    """An empty bundle means no loaded role bundle; a missing sample remains unknown."""
    value = row.get("bundle_id")
    return "UNKNOWN" if value is None else value or "NONE"


def exit_detail(row: dict) -> str:
    """Distinguish a recorded EA trigger from MT5's broad origin code."""
    reason = row.get("exit_reason", "UNKNOWN")
    if reason == "DEAL_REASON_EXPERT":
        return row.get("exit_detail") or "UNKNOWN expert trigger"
    return {"DEAL_REASON_SL": "stop_loss", "DEAL_REASON_TP": "take_profit",
            "DEAL_REASON_CLIENT": "desktop_manual", "DEAL_REASON_MOBILE": "mobile_manual",
            "DEAL_REASON_WEB": "web_manual", "DEAL_REASON_SO": "stop_out"}.get(reason, reason)


def training_status(row: dict) -> str:
    """Use persisted label quality when present; derive it read-only for legacy DBs."""
    stored = row.get("training_status")
    if stored:
        return str(stored)
    return classify_training_status(str(row.get("exit_reason", "UNKNOWN")),
                                    str(row.get("exit_detail") or ""))


def print_telemetry(trades: list[dict]) -> None:
    """Report coverage, reconciled cost components and immutable entry provenance."""
    total = len(trades)
    times = sum(all(r.get(k + "_utc_offset_seconds") is not None for k in ("opened", "closed")) for r in trades)
    print("=== TIME / DATA COVERAGE ===")
    print(f"Trades with entry and exit UTC offsets: {times}/{total}")
    print("New UTC times use event-time offsets observed by MT5; terminal OS clock must be correct.")
    print("Legacy times remain BROKER unless an offset was recorded; received is server receipt UTC, not execution time.")
    print(f"Joined entry models: {sum(bool(r.get('chronos_model')) for r in trades)}/{total}")
    print(f"Entry EA versions: {sum(bool(r.get('entry_ea_version')) for r in trades)}/{total}")
    print()
    print("=== LEARNING LABEL QUALITY ===")
    label_groups: dict[str, list[dict]] = defaultdict(list)
    for row in trades:
        label_groups[training_status(row)].append(row)
    for status, rows in sorted(label_groups.items()):
        print(f"{status:28} | trades={len(rows):3d} | net={sum(r['net_units'] for r in rows):+.4f}")
    eligible = len(label_groups.get("LEARNABLE", []))
    print(f"Eligible model labels: {eligible}/{total}")
    print("Censored trades remain in account P/L but are excluded from supervised role-model labels.")
    print()
    print("=== COST BREAKDOWN (ACCOUNT UNITS) ===")
    keys = ("profit_units", "commission_units", "swap_units", "fee_units")
    known = [r for r in trades if all(r.get(k) is not None for k in keys)]
    print(f"Complete breakdown: {len(known)}/{total}; missing components are UNKNOWN, not zero.")
    if known:
        for k in keys:
            print(f"{k:20}: {sum(r[k] for r in known):+.4f}")
        component_net = sum(sum(r[k] for k in keys) for r in known)
        recorded_net = sum(r["net_units"] for r in known)
        print(f"Component net       : {component_net:+.4f}")
        print(f"Net of covered rows : {recorded_net:+.4f}")
        print(f"Reconciliation delta: {component_net - recorded_net:+.8f}")
    print("Costs are signed broker amounts across entry + exit deals; net already includes them.")
    print("Spread/slippage are embedded in fill-price P&L, not extra deductions; separate broker balance charges may be unattributed.")
    print()
    print("=== BY EXACT EXIT TRIGGER ===")
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in trades:
        groups[(r["exit_reason"], exit_detail(r))].append(r)
    for (reason, detail), rows in sorted(groups.items()):
        print(f"{reason} / {detail} | trades={len(rows)} | net={sum(r['net_units'] for r in rows):+.4f}")
    print()
    print("=== BY ENTRY MODEL / BUNDLE / EA ===")
    groups = defaultdict(list)
    for r in trades:
        metadata = safe_json(r.get("model_metadata"))
        groups[(r.get("chronos_model") or "UNKNOWN", metadata.get("chronos_revision") or "UNKNOWN",
                bundle_label(r), r.get("entry_ea_version") or "UNKNOWN",
                metadata.get("ensemble_mode", "UNKNOWN"), metadata.get("ensemble_active", "UNKNOWN"))].append(r)
    for (model, revision, bundle, ea, mode, active), rows in sorted(groups.items(), key=lambda item: str(item[0])):
        wins = sum(r["net_units"] > 0 for r in rows)
        print(f"model={model} | revision={revision} | bundle={bundle} | EA={ea} | mode={mode} active={active}")
        print(f"  trades={len(rows)} WR={pct(wins, len(rows)):.2f}% net={sum(r['net_units'] for r in rows):+.4f} avgR={mean([r['net_r'] for r in rows]):+.4f}")
        metadata = safe_json(rows[0].get("model_metadata"))
        manifest = metadata.get("role_manifest") or {}
        if not isinstance(manifest, dict):
            manifest = {}
        print(f"  bundle_created_utc={manifest.get('created_at_utc', 'UNKNOWN')}")
        print(f"  training_label_end_broker={fmt_time(manifest.get('training_label_end'))} | validation_end_broker={fmt_time(manifest.get('holdout_end'))}")
    print("Model/bundle identity is from the entry decision, never today's active model.")
    print("Comparisons are descriptive; version groups may cover different market conditions.")
    print()


def generate_report(DB: str, SYMBOL: str = "XAUUSD_l", LIMIT: int = 20) -> None:
    """Print a consistent, read-only snapshot of trade outcomes and decision features."""
    with sqlite3.connect(Path(DB).expanduser().resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.execute("BEGIN")
        con.row_factory = sqlite3.Row

        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_outcomes'"
        ).fetchone()
        if not exists:
            raise SystemExit(f"trade_outcomes table not found in {DB}")

        trades = load_report_trades(con, SYMBOL)

        print("=" * 78)
        print("RAMON PERFORMANCE REPORT")
        print("=" * 78)
        print(f"Database : {DB}")
        print(f"Symbol   : {SYMBOL}")
        print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print()

        if not trades:
            print("No closed Ramon trades found.")
            return

        total = len(trades)
        wins = [r for r in trades if r["net_units"] > 0]
        losses = [r for r in trades if r["net_units"] < 0]
        breakeven = [r for r in trades if r["net_units"] == 0]

        net_units = sum(float(r["net_units"]) for r in trades)
        net_r = sum(float(r["net_r"]) for r in trades)
        gross_profit = sum(float(r["net_units"]) for r in wins)
        gross_loss = abs(sum(float(r["net_units"]) for r in losses))
        avg_win = mean([float(r["net_units"]) for r in wins])
        avg_loss = mean([float(r["net_units"]) for r in losses])
        avg_r = mean([float(r["net_r"]) for r in trades])
        best_r = max(float(r["net_r"]) for r in trades)
        worst_r = min(float(r["net_r"]) for r in trades)
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        expectancy_units = net_units / total
        payoff = avg_win / abs(avg_loss) if losses and avg_loss else float("inf")
        breakeven_wr = (
            abs(avg_loss) / (avg_win + abs(avg_loss)) * 100.0
            if wins and losses and avg_win + abs(avg_loss) > 0
            else float("nan")
        )

        print("=== OVERVIEW ===")
        print(f"Period              : {trade_time(min(trades, key=lambda r: r['opened']), 'opened')} -> {trade_time(max(trades, key=lambda r: r['closed']), 'closed')}")
        print(f"Closed trades       : {total}")
        print(f"Wins / Losses / BE  : {len(wins)} / {len(losses)} / {len(breakeven)}")
        print(f"Win rate            : {pct(len(wins), total):.2f}%")
        print(f"Net account units   : {net_units:+.4f}")
        print(f"Net USD approx*     : ${net_units / 100.0:+.4f}")
        print(f"Gross profit/loss   : {gross_profit:.4f} / {gross_loss:.4f}")
        print(f"Profit factor       : {pf:.3f}" if math.isfinite(pf) else "Profit factor       : inf")
        print(f"Avg win / avg loss  : {avg_win:+.4f} / {avg_loss:+.4f}")
        print(f"Payoff ratio        : {payoff:.3f}" if math.isfinite(payoff) else "Payoff ratio        : inf")
        print(f"Break-even win rate : {breakeven_wr:.2f}%" if math.isfinite(breakeven_wr) else "Break-even win rate : n/a")
        print(f"Expectancy/trade    : {expectancy_units:+.4f} units (~${expectancy_units / 100.0:+.4f})")
        print(f"Net R / Avg R       : {net_r:+.4f}R / {avg_r:+.4f}R")
        print(f"Best R / Worst R    : {best_r:+.4f}R / {worst_r:+.4f}R")
        print("* USD approximation assumes the current CENT convention: 100 account units = 1 USD.")
        print()

        print_telemetry(trades)

        print("=== BY DIRECTION ===")
        direction_rows = con.execute(
            """
            SELECT direction,
                   COUNT(*) AS trades,
                   SUM(CASE WHEN net_units>0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN net_units<0 THEN 1 ELSE 0 END) AS losses,
                   SUM(net_units) AS net_units,
                   AVG(net_r) AS avg_r,
                   SUM(net_r) AS total_r
            FROM trade_outcomes
            WHERE symbol=?
            GROUP BY direction
            ORDER BY direction
            """,
            (SYMBOL,),
        ).fetchall()
        for r in direction_rows:
            print(
                f"{r['direction']:4} | trades={r['trades']:3d} "
                f"| W/L={r['wins']}/{r['losses']} "
                f"| WR={pct(r['wins'], r['trades']):6.2f}% "
                f"| net={r['net_units']:+9.2f} "
                f"| avgR={r['avg_r']:+7.4f} "
                f"| totalR={r['total_r']:+8.4f}"
            )
        print()

        print("=== BY EXIT REASON ===")
        exit_rows = con.execute(
            """
            SELECT exit_reason,
                   COUNT(*) AS trades,
                   SUM(CASE WHEN net_units>0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN net_units<0 THEN 1 ELSE 0 END) AS losses,
                   SUM(net_units) AS net_units,
                   AVG(net_r) AS avg_r,
                   SUM(net_r) AS total_r
            FROM trade_outcomes
            WHERE symbol=?
            GROUP BY exit_reason
            ORDER BY trades DESC, exit_reason
            """,
            (SYMBOL,),
        ).fetchall()
        for r in exit_rows:
            print(
                f"{r['exit_reason']:22} | trades={r['trades']:3d} "
                f"| W/L={r['wins']}/{r['losses']} "
                f"| net={r['net_units']:+9.2f} "
                f"| avgR={r['avg_r']:+7.4f} "
                f"| totalR={r['total_r']:+8.4f}"
            )
        print()

        shown = trades if LIMIT <= 0 else trades[-LIMIT:]
        print(f"=== {'ALL' if LIMIT <= 0 else f'LAST {len(shown)}'} CLOSED TRADES ===")
        for i, r in enumerate(shown, 1):
            result = "WIN " if r["net_units"] > 0 else "LOSS" if r["net_units"] < 0 else "BE  "
            print(
                f"{i:03d} | {result} | {r['direction']:4} "
                f"| {trade_time(r, 'opened')} -> {trade_time(r, 'closed')} "
                f"| net={r['net_units']:+8.2f} "
                f"| risk={r['initial_risk_units']:7.2f} "
                f"| R={r['net_r']:+7.3f} "
                f"| {r['exit_reason']} / {exit_detail(r)}"
                f" | model={r.get('chronos_model') or 'UNKNOWN'} bundle={bundle_label(r)}"
                f" EA={r.get('entry_ea_version') or 'UNKNOWN'}"
                f" label={training_status(r)}"
            )
            costs = " ".join(
                f"{name}={float(r[name]):+.4f}" if r.get(name) is not None else f"{name}=UNKNOWN"
                for name in ("profit_units", "commission_units", "swap_units", "fee_units")
            )
            print(f"      {costs}")
        print()

        sl_rows = [r for r in trades if r["exit_reason"] == "DEAL_REASON_SL"]
        print("=== FULL SL SUMMARY ===")
        print(
            f"SL trades={len(sl_rows)} "
            f"| wins={sum(1 for r in sl_rows if r['net_units'] > 0)} "
            f"| losses={sum(1 for r in sl_rows if r['net_units'] < 0)} "
            f"| net={sum(float(r['net_units']) for r in sl_rows):+.2f} "
            f"| totalR={sum(float(r['net_r']) for r in sl_rows):+.4f}"
        )
        print()

        sample_columns = {row[1] for row in con.execute("PRAGMA table_info(decision_samples)")}
        joined = []
        if {"sample_key", "atr", "spread", "entry_features", "regime_features"} <= sample_columns:
            news_column = "s.news_features" if "news_features" in sample_columns else "NULL AS news_features"
            joined = con.execute(
                f"""SELECT t.*, s.atr,s.spread,s.entry_features,s.regime_features,{news_column}
                    FROM trade_outcomes t JOIN decision_samples s ON s.sample_key=t.sample_key
                    WHERE t.symbol=? ORDER BY t.opened""", (SYMBOL,)
            ).fetchall()

        SIGNED = [
            ("entry", "intrabar_move_atr"),
            ("entry", "ai_trend_score"),
            ("regime", "ret_1_atr"),
            ("regime", "ret_4_atr"),
            ("regime", "ret_12_atr"),
        ]
        PLAIN = [
            ("entry", "edge_ratio"),
            ("entry", "signal_strength"),
            ("entry", "uncertainty_atr"),
            ("entry", "intrabar_rebound_atr"),
            ("entry", "ai_trend_consistency"),
            ("entry", "spread_atr"),
            ("entry", "forecast_distance_atr"),
            ("regime", "range_4_atr"),
            ("regime", "range_12_atr"),
            ("regime", "body_efficiency_12"),
            ("regime", "atr_pct"),
            ("news", "high_impact_near"),
            ("news", "medium_impact_near"),
            ("news", "upcoming_high_60m"),
            ("news", "recent_high_60m"),
            ("news", "event_density_180m"),
        ]

        feature_records: list[tuple[str, dict[str, float]]] = []
        sl_feature_records: list[tuple[dict, dict[str, float]]] = []

        for r in joined:
            r = dict(r)
            if training_status(r) != "LEARNABLE":
                continue
            entry = safe_json(r["entry_features"])
            regime = safe_json(r["regime_features"])
            news = safe_json(r["news_features"])
            src = {"entry": entry, "regime": regime, "news": news}
            sign = 1.0 if r["direction"] == "BUY" else -1.0
            features: dict[str, float] = {
                "atr": float(r["atr"]),
                "spread": float(r["spread"]),
            }

            for group, key in SIGNED:
                if key in src[group]:
                    features[f"{group}.{key}.aligned"] = float(src[group][key]) * sign

            for group, key in PLAIN:
                if key in src[group]:
                    features[f"{group}.{key}"] = float(src[group][key])

            result = "WIN" if r["net_units"] > 0 else "LOSS" if r["net_units"] < 0 else "BE"
            feature_records.append((result, features))
            if r["exit_reason"] == "DEAL_REASON_SL":
                sl_feature_records.append((r, features))

        fwins = [features for result, features in feature_records if result == "WIN"]
        flosses = [features for result, features in feature_records if result == "LOSS"]

        print("=== DIRECTION-ALIGNED WIN vs LOSS FEATURES ===")
        print(f"Joined samples: {len(feature_records)} | wins={len(fwins)} | losses={len(flosses)} (eligible labels only)")
        print("effect > 0 => feature higher in winners; effect < 0 => lower in winners")
        print("Exploratory only: small samples can produce unstable effect sizes.")
        print()

        if fwins and flosses:
            names = sorted(set().union(*(x.keys() for x in fwins + flosses)))
            comparison = []
            for name in names:
                w = [x[name] for x in fwins if name in x]
                l = [x[name] for x in flosses if name in x]
                if not w or not l:
                    continue
                effect = pooled_effect(w, l)
                comparison.append((name, mean(w), mean(l), effect))

            comparison.sort(
                key=lambda row: abs(row[3]) if math.isfinite(row[3]) else -1.0,
                reverse=True,
            )

            for name, w, l, effect in comparison:
                effect_text = f"{effect:+6.2f}" if math.isfinite(effect) else "   n/a"
                print(
                    f"{name:44} WIN={w:9.4f} LOSS={l:9.4f} effect={effect_text}"
                )
        else:
            print("Not enough joined winning/losing samples for feature comparison.")

        print()
        print("=== FULL-SL ENTRY FEATURES ===")
        if not sl_feature_records:
            print("No full-SL trades.")
        else:
            keys = [
                "entry.edge_ratio",
                "entry.signal_strength",
                "entry.uncertainty_atr",
                "entry.intrabar_move_atr.aligned",
                "entry.ai_trend_score.aligned",
                "entry.ai_trend_consistency",
                "entry.spread_atr",
                "entry.forecast_distance_atr",
                "regime.ret_4_atr.aligned",
                "regime.ret_12_atr.aligned",
                "regime.body_efficiency_12",
                "regime.range_12_atr",
            ]
            for r, features in sl_feature_records:
                print(
                    f"{trade_time(r, 'opened')} {r['direction']} "
                    f"net={r['net_units']:+.2f} R={r['net_r']:+.3f}"
                )
                for key in keys:
                    if key in features:
                        print(f"  {key:42} {features[key]:.4f}")
                print()

        print("=" * 78)
        print("END RAMON REPORT")
        print("=" * 78)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Ramon performance and provenance report")
    parser.add_argument("--db", default=os.getenv("RAMON_REPORT_DB", "/data/ramon_history.sqlite3"))
    parser.add_argument("--symbol", default=os.getenv("RAMON_REPORT_SYMBOL", "XAUUSD_l"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("RAMON_REPORT_LIMIT", "20")))
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    generate_report(args.db, args.symbol, 0 if args.all else args.limit)


if __name__ == "__main__":
    main()
