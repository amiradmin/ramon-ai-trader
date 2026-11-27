"""Read-only market-data laboratory. No MT5 connection, command polling or DB writes."""
import argparse
import json
import math
import os
import sys
import time as clock
from datetime import date, datetime, time, timedelta, timezone

import django
import psycopg


def positive(value):
    v = float(value)
    if not math.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError("must be positive and finite")
    return v


def nonnegative(value):
    v = float(value)
    if not math.isfinite(v) or v < 0:
        raise argparse.ArgumentTypeError("must be nonnegative and finite")
    return v


def parse():
    p = argparse.ArgumentParser(description="Isolated M15 momentum/pullback research; never trades")
    p.add_argument("--list-data", action="store_true",
                   help="List stored OHLC source/symbol/timeframe coverage; no replay")
    p.add_argument("--source")
    p.add_argument("--symbol")
    p.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)
    p.add_argument("--balance", type=positive)
    p.add_argument("--spread-points", type=positive)
    p.add_argument("--point", type=positive)
    p.add_argument("--tick-size", type=positive)
    p.add_argument("--tick-value", type=positive)
    p.add_argument("--volume-min", type=positive)
    p.add_argument("--volume-max", type=positive)
    p.add_argument("--volume-step", type=positive)
    p.add_argument("--slippage-points", type=nonnegative, default=2)
    p.add_argument("--commission-per-lot", type=nonnegative, default=0)
    p.add_argument("--risk-percent", type=positive, default=.5)
    p.add_argument("--cost-stress", action="store_true",
                   help="Re-evaluate both strategies with larger modeled spread/slippage")
    p.add_argument("--spread-shadow", action="store_true",
                   help="Research-only matched-spread 50/90/130-point limit shadow experiments")
    p.add_argument("--cost-attribution", action="store_true",
                   help="Separate spread vs slippage effects with fixed 1x/2x/3x scenarios; research only")
    p.add_argument("--batch-size", type=int, default=2000,
                   help="Read historical rows from PostgreSQL in bounded batches")
    args = p.parse_args()
    if sum(bool(x) for x in (args.cost_attribution, args.cost_stress, args.spread_shadow))>1:
        p.error("Choose only one of --cost-attribution, --cost-stress or --spread-shadow")
    if args.list_data:
        return args
    required = ("source","symbol","start","end","balance","spread_points",
                "point","tick_size","tick_value","volume_min","volume_max","volume_step")
    if any(getattr(args, k) is None for k in required):
        p.error("Replay requires source, symbol, dates, balance, spread and broker contract values")
    if (not args.source.strip() or not args.symbol.strip() or len(args.source)>128
            or len(args.symbol)>32 or args.start>args.end or args.end>date.today()
            or args.volume_max<args.volume_min or args.risk_percent>.5
            or not 100 <= args.batch_size <= 10000):
        p.error("Invalid market source, date range, volume limits or risk limit")
    return args


def progress(stage, done=None, total=None):
    # stderr is visible even when JSON stdout is redirected.
    if done is None or total is None:
        print(f"[LAB] {stage}", file=sys.stderr, flush=True)
    else:
        print(f"[LAB] {stage}: {done:,}/{total:,} bars ({done/total:.0%})",
              file=sys.stderr, flush=True)


def replay_progress(stage):
    last=[-1]
    def emit(done, total):
        # The existing replay invokes callbacks frequently: log only every
        # 10 percentage points, plus the final completion event.
        step=min(10, int(100*done/max(total,1))//10)
        if step>last[0]:
            last[0]=step
            progress(stage, done, total)
    return emit


def main():
    args = parse()
    progress('Starting isolated read-only laboratory')
    # Harden even if environment is accidentally misconfigured.
    if os.environ.get("DJANGO_DB_USER") != "ramon_lab_reader":
        raise RuntimeError("LAB_DB_READER_REQUIRED")
    if any(os.environ.get(k) == "1" for k in (
            "RAMON_EA_COMMAND_CREATION_ENABLED", "RAMON_EA_COMMAND_EXECUTION_ENABLED",
            "RAMON_STRATEGY_CONTAINERS_ENABLED", "RAMON_SCALP_EXECUTION_ENABLED")):
        raise RuntimeError("LIVE_TRADING_SWITCH_FORBIDDEN")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ramon_django.settings")
    django.setup()
    from ea_bridge.replay_backtest import _simulate, _clean_m15_rows, WARMUP
    from impulse_continuation_research import impulse_continuation_research

    params = {"host":os.environ["DJANGO_DB_HOST"],
              "port":int(os.environ.get("DJANGO_DB_PORT","5432")),
              "dbname":os.environ["DJANGO_DB_NAME"],
              "user":os.environ["DJANGO_DB_USER"],
              "password":os.environ["DJANGO_DB_PASSWORD"],
              "connect_timeout":5, "options":"-c default_transaction_read_only=on -c statement_timeout=120000"}
    if args.list_data:
        with psycopg.connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("""
                    SELECT source, symbol, timeframe, COUNT(*), MIN(timestamp), MAX(timestamp)
                    FROM public.ea_bridge_marketbar
                    GROUP BY source, symbol, timeframe
                    ORDER BY source, symbol, timeframe
                """)
                coverage = cur.fetchall()
        print(json.dumps({"mode":"READ_ONLY_LAB_DATA_CATALOG","datasets":[
            {"source":r[0],"symbol":r[1],"timeframe":r[2],"candles":r[3],
             "first_utc":r[4].isoformat(),"last_utc":r[5].isoformat()}
            for r in coverage]}, indent=2))
        return
    start = datetime.combine(args.start, time.min, tzinfo=timezone.utc)
    end = datetime.combine(args.end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    # Fetch long time series in bounded batches. Evaluation is continuous
    # across fetch boundaries: indicator warm-up, pending orders and positions
    # never reset at a batch boundary.
    progress('Reading M15 candles from PostgreSQL')
    selected=[]
    batches=0
    with psycopg.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("""
                SELECT timestamp, open, high, low, close
                FROM public.ea_bridge_marketbar
                WHERE source=%s AND symbol=%s AND timeframe='M15'
                  AND timestamp < %s
                ORDER BY timestamp DESC LIMIT 256
            """, (args.source, args.symbol, start))
            prior = list(reversed(cur.fetchall()))
        with conn.cursor(name="lab_m15_stream") as cur:
            cur.execute("""
                SELECT timestamp, open, high, low, close
                FROM public.ea_bridge_marketbar
                WHERE source=%s AND symbol=%s AND timeframe='M15'
                  AND timestamp >= %s AND timestamp < %s
                ORDER BY timestamp
            """, (args.source, args.symbol, start, end))
            while True:
                batch=cur.fetchmany(args.batch_size)
                if not batch:
                    break
                selected.extend(batch)
                batches+=1
                if len(selected)>120000:
                    raise RuntimeError("MAX_120000_M15_BARS_EXCEEDED")
    progress(f'Loaded {len(selected):,} candles in {batches} database batches')
    if not selected:
        raise RuntimeError("NO_SELECTED_M15_BARS: inspect --list-data for source/symbol/date coverage")
    rows = [dict(time=int(r[0].timestamp()),open=float(r[1]),high=float(r[2]),
                 low=float(r[3]),close=float(r[4])) for r in prior+selected]
    rows, quality = _clean_m15_rows(rows)
    if len(rows) < WARMUP+2:
        raise RuntimeError("INSUFFICIENT_VALID_M15_HISTORY")
    data = {
        "source":args.source,"symbol":args.symbol,"start":args.start,"end":args.end,
        "balance":args.balance,"spread_points":args.spread_points,
        "point":args.point,"tick_size":args.tick_size,"tick_value":args.tick_value,
        "volume_min":args.volume_min,"volume_max":args.volume_max,
        "volume_step":args.volume_step,"slippage_points":args.slippage_points,
        "commission_per_lot":args.commission_per_lot,"risk_percent":args.risk_percent,
        "setups":["TREND_PULLBACK"],"stop_buffer_atr":.25,
        "target_reward_risk":1.25,"minimum_opportunity_score":.55,
        "minimum_entry_quality":.55,
    }
    from segmented import breakdown, fingerprint, quality_gate
    from diagnostics import (gap_diagnostics, risk_diagnostics, contract_diagnostics,
                             per_year, per_regime, sensitivity_metric)
    progress('Running TREND_PULLBACK, baseline costs')
    baseline = _simulate(data, rows, progress=replay_progress('TREND_PULLBACK 1x'))[0]
    progress('Completed TREND_PULLBACK, baseline costs')
    if args.spread_shadow:
        import ea_bridge.replay_backtest as replay_module
        from spread_shadow import comparison_report
        progress('Running frozen higher-spread shadow experiment')
        shadow=comparison_report(
            replay_module=replay_module, data=data, rows=rows,
            start=args.start,end=args.end,baseline=baseline,
            quality=quality,
            progress_callback=lambda name: progress(f'Shadow scenario {name}: starting'),
            progress_factory=lambda name: replay_progress(f'Shadow scenario {name}'))
        shadow.update({
            "market_source":args.source,"symbol":args.symbol,
            "start":args.start.isoformat(),"end":args.end.isoformat(),
            "history_fingerprint":quality["history_fingerprint"],
            "selected_candles":len(selected),
            "database_fetch_batches":batches,
            "history_quality":quality,
            "broker_assumptions":{k:data[k] for k in (
                "balance","spread_points","point","tick_size","tick_value",
                "volume_min","volume_max","volume_step","risk_percent",
                "slippage_points","commission_per_lot")}
        })
        progress('Writing shadow experiment JSON')
        print(json.dumps(shadow,indent=2,sort_keys=True))
        progress('Completed successfully')
        return
    progress('Running IMPULSE_CONTINUATION, baseline costs')
    candidate = impulse_continuation_research(
        rows, start_ts=int(start.timestamp()), end_ts=int(end.timestamp()),
        balance=args.balance, spread_points=args.spread_points, point=args.point,
        tick_size=args.tick_size, tick_value=args.tick_value,
        volume_min=args.volume_min, volume_max=args.volume_max,
        volume_step=args.volume_step, risk_percent=args.risk_percent,
        slippage_points=args.slippage_points,
        commission_per_lot=args.commission_per_lot)
    progress('Completed IMPULSE_CONTINUATION, baseline costs')
    # Fixed predeclared chronological partitions. The original strategy and
    # candidate are each replayed exactly ONCE across the full timeline.
    # This avoids resets or phantom trading at PostgreSQL batch boundaries.
    baseline_periods = breakdown(baseline, args.start, args.end, args.balance,
                                 name="TREND_PULLBACK")
    candidate_periods = breakdown(candidate, args.start, args.end, args.balance,
                                  name="IMPULSE_CONTINUATION")
    def summarize(x):
        return {k:x.get(k) for k in ("signals","closed_trades","win_rate","net_gain",
                                     "profit_factor","max_drawdown_pct","open_position")}
    gate=quality_gate(quality, baseline_periods,
                      {**candidate_periods,"status":candidate.get("status"),
                       "open_position":candidate.get("open_position")},
                      min_test_trades=30)
    stress = []
    if args.cost_attribution:
        # Factorial, predeclared scenarios: vary one modeled cost at a
        # time to isolate rejection effects. NEVER relax spread checks,
        # risk limits, score thresholds or any live settings.
        scenarios = (("BASE_COST", 1, 1),
                     ("SPREAD_ONLY_2X", 2, 1),
                     ("SLIPPAGE_ONLY_2X", 1, 2),
                     ("DOUBLE_COST", 2, 2),
                     ("SPREAD_ONLY_3X", 3, 1),
                     ("SLIPPAGE_ONLY_3X", 1, 3),
                     ("TRIPLE_COST", 3, 3))
    elif args.cost_stress:
        scenarios = (("BASE_COST", 1, 1), ("DOUBLE_COST", 2, 2),
                     ("TRIPLE_COST", 3, 3))
    else:
        scenarios = ()
    for name, spread_multiple, slippage_multiple in scenarios:
        stressed=dict(data,
                      spread_points=data["spread_points"]*spread_multiple,
                      slippage_points=data["slippage_points"]*slippage_multiple)
        progress(f'Cost scenario {name}: starting')
        trend = baseline if (spread_multiple, slippage_multiple)==(1,1) else _simulate(
            stressed,rows,progress=replay_progress(f'TREND_PULLBACK {name}'))[0]
        impulse = candidate if (spread_multiple, slippage_multiple)==(1,1) else impulse_continuation_research(
            rows, start_ts=int(start.timestamp()), end_ts=int(end.timestamp()),
            balance=args.balance, spread_points=stressed["spread_points"],
            point=args.point, tick_size=args.tick_size, tick_value=args.tick_value,
            volume_min=args.volume_min, volume_max=args.volume_max,
            volume_step=args.volume_step, risk_percent=args.risk_percent,
            slippage_points=stressed["slippage_points"],
            commission_per_lot=args.commission_per_lot)
        progress(f'Cost scenario {name}: completed')
        stress.append({"name":name,
                       "spread_multiple":spread_multiple,
                       "slippage_multiple":slippage_multiple,
                       "spread_points":stressed["spread_points"],
                       "slippage_points":stressed["slippage_points"],
                       "trend_pullback":sensitivity_metric(trend),
                       "impulse_continuation":sensitivity_metric(impulse)})
    output = {
        "mode":"ISOLATED_READ_ONLY_LABORATORY", "trading_authorized":False,
        "gap_diagnostics":gap_diagnostics(rows),
        "contract_diagnostics":contract_diagnostics(args),
        "candidate_risk_diagnostics":risk_diagnostics(candidate),
        "annual_performance":{
            "trend_pullback":per_year(baseline,initial_balance=args.balance),
            "impulse_continuation":per_year(candidate,initial_balance=args.balance)},
        "regime_performance":{
            "trend_pullback":per_regime(baseline,initial_balance=args.balance),
            "impulse_continuation":per_regime(candidate,initial_balance=args.balance)},
        "cost_sensitivity":stress,
        "cost_sensitivity_status":"ATTRIBUTION_REPLAYED" if args.cost_attribution else ("REPLAYED" if args.cost_stress else "NOT_REQUESTED"),
        "cost_experiment_method":"FIXED_SEPARATE_SPREAD_AND_SLIPPAGE_NO_THRESHOLD_TUNING" if args.cost_attribution else "JOINT_COST_SCALING",
        "market_source":args.source,"symbol":args.symbol,
        "start":args.start.isoformat(),"end":args.end.isoformat(),
        "data_quality":quality,
        "data_quality_warning":quality["gap_count"]>0 or any(quality["excluded"].values()),
        "history_fingerprint":fingerprint(rows),
        "history_rows":len(rows),
        "selected_candles":len(selected),
        "database_fetch_batches":batches,
        "batch_size":args.batch_size,
        "partition_method":"FIXED_CALENDAR_TRADE_ENTRY_DATE",
        "full_timeline_replay":True,
        "historical_period_comparisons":[baseline_periods,candidate_periods],
        "live_eligibility":gate,
        "assumptions":{k:data[k] for k in ("balance","spread_points","point",
            "tick_size","tick_value","volume_min","volume_max","volume_step",
            "slippage_points","commission_per_lot","risk_percent")},
        "trend_pullback":summarize(baseline),
        "impulse_continuation":summarize(candidate),
        "candidate_status":candidate.get("status"),
        "candidate_rejections":candidate.get("rejections"),
        "limitations":"Historical bid-only OHLC and modeled costs; not broker-verified; gaps may invalidate comparisons; cent-account units require broker verification. No live execution."
    }
    progress('Writing final JSON result')
    print(json.dumps(output,indent=2,sort_keys=True))
    progress('Completed successfully')


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print("LAB_ERROR:",str(exc),file=sys.stderr)
        sys.exit(1)
