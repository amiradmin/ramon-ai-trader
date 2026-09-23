#!/usr/bin/env bash
set -euo pipefail

# Ramon performance report.
# Usage:
#   bash scripts/analyze_ramon.sh
#   bash scripts/analyze_ramon.sh --all
#   RAMON_REPORT_LIMIT=50 bash scripts/analyze_ramon.sh
#
# Optional env:
#   RAMON_HISTORY_DB=/data/ramon_history.sqlite3
#   RAMON_SYMBOL=XAUUSD_l
#   RAMON_REPORT_LIMIT=20

DB="${RAMON_HISTORY_DB:-/data/ramon_history.sqlite3}"
SYMBOL="${RAMON_SYMBOL:-XAUUSD_l}"
LIMIT="${RAMON_REPORT_LIMIT:-20}"

if [[ "${1:-}" == "--all" ]]; then
  LIMIT=0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed or not in PATH." >&2
  exit 1
fi

if ! docker compose ps --status running --services 2>/dev/null | grep -qx "model"; then
  echo "ERROR: docker compose service 'model' is not running." >&2
  echo "Start it with: docker compose up -d model" >&2
  exit 1
fi

docker compose exec -T \
  -e RAMON_REPORT_DB="$DB" \
  -e RAMON_REPORT_SYMBOL="$SYMBOL" \
  -e RAMON_REPORT_LIMIT="$LIMIT" \
  model python - <<'PY'
from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
from datetime import datetime

DB = os.environ.get("RAMON_REPORT_DB", "/data/ramon_history.sqlite3")
SYMBOL = os.environ.get("RAMON_REPORT_SYMBOL", "XAUUSD_l")
LIMIT = int(os.environ.get("RAMON_REPORT_LIMIT", "20"))


def pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else 0.0


def fmt_time(value: int | None) -> str:
    if not value:
        return "-"
    return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M")


def safe_json(value: str | None) -> dict[str, float]:
    if not value:
        return {}
    try:
        raw = json.loads(value)
        return raw if isinstance(raw, dict) else {}
    except Exception:
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


with sqlite3.connect(DB) as con:
    con.row_factory = sqlite3.Row

    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_outcomes'"
    ).fetchone()
    if not exists:
        raise SystemExit(f"trade_outcomes table not found in {DB}")

    trades = con.execute(
        """
        SELECT trade_key,sample_key,symbol,direction,opened,closed,
               net_units,initial_risk_units,net_r,exit_reason,received
        FROM trade_outcomes
        WHERE symbol=?
        ORDER BY opened
        """,
        (SYMBOL,),
    ).fetchall()

    print("=" * 78)
    print("RAMON PERFORMANCE REPORT")
    print("=" * 78)
    print(f"Database : {DB}")
    print(f"Symbol   : {SYMBOL}")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    if not trades:
        print("No closed Ramon trades found.")
        raise SystemExit(0)

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
    print(f"Period              : {fmt_time(trades[0]['opened'])} -> {fmt_time(trades[-1]['closed'])}")
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
            f"| {fmt_time(r['opened'])} -> {fmt_time(r['closed'])} "
            f"| net={r['net_units']:+8.2f} "
            f"| risk={r['initial_risk_units']:7.2f} "
            f"| R={r['net_r']:+7.3f} "
            f"| {r['exit_reason']}"
        )
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

    joined = con.execute(
        """
        SELECT t.direction,t.opened,t.net_units,t.net_r,t.exit_reason,
               s.atr,s.spread,s.entry_features,s.regime_features,s.news_features
        FROM trade_outcomes t
        JOIN decision_samples s ON s.sample_key=t.sample_key
        WHERE t.symbol=?
        ORDER BY t.opened
        """,
        (SYMBOL,),
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
    sl_feature_records: list[tuple[sqlite3.Row, dict[str, float]]] = []

    for r in joined:
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
    print(f"Joined samples: {len(joined)} | wins={len(fwins)} | losses={len(flosses)}")
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
                f"{fmt_time(r['opened'])} {r['direction']} "
                f"net={r['net_units']:+.2f} R={r['net_r']:+.3f}"
            )
            for key in keys:
                if key in features:
                    print(f"  {key:42} {features[key]:.4f}")
            print()

    print("=" * 78)
    print("END RAMON REPORT")
    print("=" * 78)
PY
