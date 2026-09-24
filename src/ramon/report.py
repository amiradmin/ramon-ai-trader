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
    names = ("chronos_model", "bundle_id", "model_metadata", "captured", "mid", "spread", "stop_distance")
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


def drawdown_metrics(trades: list[dict]) -> tuple[float, float]:
    """Return maximum closed-trade peak-to-trough drawdown in account units and R."""
    equity_units = equity_r = 0.0
    peak_units = peak_r = 0.0
    max_dd_units = max_dd_r = 0.0
    for row in trades:
        equity_units += float(row["net_units"])
        equity_r += float(row["net_r"])
        peak_units = max(peak_units, equity_units)
        peak_r = max(peak_r, equity_r)
        max_dd_units = max(max_dd_units, peak_units - equity_units)
        max_dd_r = max(max_dd_r, peak_r - equity_r)
    return max_dd_units, max_dd_r


def loss_streak_metrics(trades: list[dict]) -> dict[str, float | int]:
    """Return the longest consecutive closed-trade losing streak."""
    best = {"length": 0, "start": 0, "end": 0, "net_units": 0.0, "net_r": 0.0}
    start = 0
    length = 0
    net_units = 0.0
    net_r = 0.0
    for index, row in enumerate(trades, 1):
        if float(row["net_units"]) < 0:
            if length == 0:
                start = index
            length += 1
            net_units += float(row["net_units"])
            net_r += float(row["net_r"])
            if length > int(best["length"]) or (
                length == int(best["length"]) and net_units < float(best["net_units"])
            ):
                best = {
                    "length": length,
                    "start": start,
                    "end": index,
                    "net_units": net_units,
                    "net_r": net_r,
                }
        else:
            length = 0
            net_units = 0.0
            net_r = 0.0
    return best


def rolling_trade_metrics(trades: list[dict], window: int = 20) -> list[dict[str, float | int]]:
    """Calculate contiguous rolling closed-trade PF and expectancy."""
    if window <= 0 or len(trades) < window:
        return []
    rows: list[dict[str, float | int]] = []
    for end in range(window, len(trades) + 1):
        subset = trades[end - window:end]
        gains = sum(max(float(row["net_units"]), 0.0) for row in subset)
        losses = abs(sum(min(float(row["net_units"]), 0.0) for row in subset))
        rows.append({
            "start": end - window + 1,
            "end": end,
            "pf": gains / losses if losses > 0 else float("inf"),
            "expectancy_units": sum(float(row["net_units"]) for row in subset) / window,
            "expectancy_r": sum(float(row["net_r"]) for row in subset) / window,
            "net_units": sum(float(row["net_units"]) for row in subset),
            "win_rate": pct(sum(float(row["net_units"]) > 0 for row in subset), window),
        })
    return rows


def load_bar_excursions(
    con: sqlite3.Connection, trades: list[dict], symbol: str
) -> dict[str, dict[str, float | int]]:
    """Approximate MFE/MAE from stored M15 bar envelopes and entry decision quotes.

    Boundary M15 bars can contain prices from before entry or after exit, so these
    are deliberately labelled approximations rather than exact tick-level excursions.
    """
    if not trades:
        return {}
    tables = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "history_bars" not in tables:
        return {}
    columns = {row[1] for row in con.execute("PRAGMA table_info(history_bars)")}
    if not {"symbol", "timeframe", "time", "high", "low"} <= columns:
        return {}

    start = min(int(row["opened"]) for row in trades) - 900
    end = max(int(row["closed"]) for row in trades)
    bars = [
        dict(row) for row in con.execute(
            """SELECT time,high,low FROM history_bars
               WHERE symbol=? AND timeframe='M15' AND time>=? AND time<=?
               ORDER BY time""",
            (symbol, start, end),
        )
    ]
    if not bars:
        return {}

    excursions: dict[str, dict[str, float | int]] = {}
    for row in trades:
        mid = row.get("mid")
        spread = row.get("spread")
        stop_distance = row.get("stop_distance")
        if mid is None or spread is None or stop_distance is None or float(stop_distance) <= 0:
            continue
        overlapping = [
            bar for bar in bars
            if int(bar["time"]) <= int(row["closed"])
            and int(bar["time"]) + 900 > int(row["opened"])
        ]
        if not overlapping:
            continue
        direction = str(row["direction"])
        entry = float(mid) + float(spread) / 2.0 if direction == "BUY" else float(mid) - float(spread) / 2.0
        high = max(float(bar["high"]) for bar in overlapping)
        low = min(float(bar["low"]) for bar in overlapping)
        if direction == "BUY":
            favorable = max(0.0, high - entry)
            adverse = max(0.0, entry - low)
        else:
            favorable = max(0.0, entry - low)
            adverse = max(0.0, high - entry)
        stop = float(stop_distance)
        excursions[str(row["trade_key"])] = {
            "mfe_r": favorable / stop,
            "mae_r": adverse / stop,
            "bars": len(overlapping),
        }
    return excursions


def print_path_risk(trades: list[dict]) -> None:
    max_dd_units, max_dd_r = drawdown_metrics(trades)
    streak = loss_streak_metrics(trades)
    print("=== DRAWDOWN / LOSS STREAK ===")
    print(f"Maximum closed-trade DD : {max_dd_units:.4f} units (~${max_dd_units / 100.0:.4f})")
    print(f"Maximum closed-trade DD : {max_dd_r:.4f}R")
    if int(streak["length"]):
        print(
            f"Max consecutive losses  : {int(streak['length'])} trades "
            f"(#{int(streak['start'])}-#{int(streak['end'])}) "
            f"| net={float(streak['net_units']):+.4f} units "
            f"| totalR={float(streak['net_r']):+.4f}R"
        )
    else:
        print("Max consecutive losses  : 0")
    print("Drawdown uses cumulative CLOSED trades from a zero P/L baseline; open-position drawdown is excluded.")
    print()


def print_rolling_performance(trades: list[dict], window: int = 20) -> None:
    rows = rolling_trade_metrics(trades, window)
    print(f"=== ROLLING {window}-TRADE PERFORMANCE ===")
    if not rows:
        print(f"Need at least {window} closed trades; currently {len(trades)}.")
        print()
        return
    latest = rows[-1]
    best = max(rows, key=lambda row: float(row["expectancy_r"]))
    worst = min(rows, key=lambda row: float(row["expectancy_r"]))
    def line(label: str, row: dict[str, float | int]) -> None:
        pf_value = float(row["pf"])
        pf_text = f"{pf_value:.3f}" if math.isfinite(pf_value) else "inf"
        print(
            f"{label:7} #{int(row['start'])}-{int(row['end'])} "
            f"| PF={pf_text:>6} | WR={float(row['win_rate']):6.2f}% "
            f"| expectancy={float(row['expectancy_units']):+.4f} units/trade "
            f"| avgR={float(row['expectancy_r']):+.4f}R "
            f"| net={float(row['net_units']):+.4f}"
        )
    line("Latest", latest)
    line("Best", best)
    line("Worst", worst)
    print("Best/worst are selected by rolling average R, descriptively; they are not model-selection evidence.")
    print()


def print_excursion_summary(
    trades: list[dict], excursions: dict[str, dict[str, float | int]]
) -> None:
    print("=== MAE / MFE (M15 BAR-ENVELOPE APPROXIMATION) ===")
    print(f"Coverage: {len(excursions)}/{len(trades)} closed trades")
    print("Uses decision-side quote plus stored M15 high/low and initial stop distance.")
    print("Boundary bars may include prices before entry/after exit; values are approximate, not tick-exact.")
    if not excursions:
        print("No compatible entry quote/stop-distance + M15 bar coverage.")
        print()
        return
    for label, subset in (
        ("ALL", trades),
        ("WIN", [row for row in trades if float(row["net_units"]) > 0]),
        ("LOSS", [row for row in trades if float(row["net_units"]) < 0]),
    ):
        values = [excursions.get(str(row["trade_key"])) for row in subset]
        values = [value for value in values if value is not None]
        if not values:
            continue
        mfes = [float(value["mfe_r"]) for value in values]
        maes = [float(value["mae_r"]) for value in values]
        print(
            f"{label:4} | trades={len(values):3d} "
            f"| avgMFE={mean(mfes):.3f}R medMFE={statistics.median(mfes):.3f}R "
            f"| avgMAE={mean(maes):.3f}R medMAE={statistics.median(maes):.3f}R"
        )
    print()


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
    print(f"Clean exit labels: {eligible}/{total}")
    print("Trainer eligibility also requires a matching model/schema/direction and entry within 90s of the decision.")
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


def print_stored_sizing(trades: list[dict]) -> None:
    """Report exact entry sizing persisted by EA 0.29+; legacy rows stay unknown."""
    fields = (
        "risk_budget_units", "planned_volume", "min_lot_sl_units",
        "min_lot_override_used", "max_executable_risk_usd", "money_units_per_usd",
    )
    known = [row for row in trades if all(row.get(name) is not None for name in fields)]
    print("=== EXACT STORED ENTRY SIZING ===")
    print(f"Exact sizing coverage: {len(known)}/{len(trades)} closed trades")
    print("EA 0.29+ stores these fields by sample_key via immutable opening-deal telemetry.")
    print("Legacy rows without exact sizing remain UNKNOWN; CSV candidate analysis is separate.")
    if not known:
        print("No exact sizing telemetry has reached SQLite yet.")
        print()
        return
    for flag in (1, 0):
        rows = [row for row in known if int(row["min_lot_override_used"]) == flag]
        label = "YES" if flag else "NO"
        if not rows:
            print(f"override={label:3} | trades=0")
            continue
        wins = sum(float(row["net_units"]) > 0 for row in rows)
        losses = sum(float(row["net_units"]) < 0 for row in rows)
        risk_ratios = [
            float(row["initial_risk_units"]) / float(row["risk_budget_units"])
            for row in rows if float(row["risk_budget_units"]) > 0
        ]
        print(
            f"override={label:3} | trades={len(rows):3d} "
            f"| W/L={wins}/{losses} | WR={pct(wins, len(rows)):.2f}% "
            f"| net={sum(float(row['net_units']) for row in rows):+.4f} "
            f"| avgR={mean([float(row['net_r']) for row in rows]):+.4f} "
            f"| avgRisk={mean([float(row['initial_risk_units']) for row in rows]):.4f} "
            f"| avgRisk/budget={mean(risk_ratios):.3f}x"
        )
    print("Exact sizing is descriptive provenance; it does not alter execution or trainer labels.")
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

        excursions = load_bar_excursions(con, trades, SYMBOL)
        print_path_risk(trades)
        print_rolling_performance(trades, 20)
        print_excursion_summary(trades, excursions)

        print_telemetry(trades)
        print_stored_sizing(trades)

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
                + (
                    f" | MFE~{float(excursions[str(r['trade_key'])]['mfe_r']):.3f}R"
                    f" MAE~{float(excursions[str(r['trade_key'])]['mae_r']):.3f}R"
                    if str(r["trade_key"]) in excursions else ""
                )
            )
            costs = " ".join(
                f"{name}={float(r[name]):+.4f}" if r.get(name) is not None else f"{name}=UNKNOWN"
                for name in ("profit_units", "commission_units", "swap_units", "fee_units")
            )
            print(f"      {costs}")
            print(f"      trade_key={r['trade_key']} sample_key={r['sample_key']}")
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
                    FROM trade_outcomes t JOIN decision_samples s ON s.sample_key=t.sample_key AND s.symbol=t.symbol
                    WHERE t.symbol=? ORDER BY t.opened""", (SYMBOL,)
            ).fetchall()

        SIGNED = [
            ("regime", "ret_1_atr"),
            ("regime", "ret_4_atr"),
            ("regime", "ret_12_atr"),
        ]
        PLAIN = [
            ("entry", "ai_trend_score"),
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

            # _intrabar_metrics already measures movement toward the candidate side.
            # _model_trend_metrics returns a NONNEGATIVE score, not a signed return.
            # Multiplying either by SELL's -1 invents a directional difference.
            if "intrabar_move_atr" in entry:
                features["entry.intrabar_move_atr.aligned"] = float(entry["intrabar_move_atr"])

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
        print(f"Joined samples: {len(feature_records)} | wins={len(fwins)} | losses={len(flosses)} (clean exit labels only)")
        print("effect > 0 => feature higher in winners; effect < 0 => lower in winners")
        print("Exploratory only: small samples can produce unstable effect sizes.")
        print("Intrabar move is already direction-aligned; ai_trend_score is magnitude only (legacy trend direction is not stored).")
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
                "entry.ai_trend_score",
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
