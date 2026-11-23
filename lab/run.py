"""Read-only market-data laboratory. No MT5 connection, command polling or DB writes."""
import argparse
import json
import math
import os
import sys
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
    p.add_argument("--source", required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--balance", type=positive, required=True)
    p.add_argument("--spread-points", type=positive, required=True)
    p.add_argument("--point", type=positive, required=True)
    p.add_argument("--tick-size", type=positive, required=True)
    p.add_argument("--tick-value", type=positive, required=True)
    p.add_argument("--volume-min", type=positive, required=True)
    p.add_argument("--volume-max", type=positive, required=True)
    p.add_argument("--volume-step", type=positive, required=True)
    p.add_argument("--slippage-points", type=nonnegative, default=2)
    p.add_argument("--commission-per-lot", type=nonnegative, default=0)
    p.add_argument("--risk-percent", type=positive, default=.5)
    args = p.parse_args()
    if (not args.source.strip() or not args.symbol.strip() or len(args.source)>128
            or len(args.symbol)>32 or args.start>args.end or args.end>date.today()
            or args.volume_max<args.volume_min or args.risk_percent>.5):
        p.error("Invalid market source, date range, volume limits or risk limit")
    return args


def main():
    args = parse()
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

    start = datetime.combine(args.start, time.min, tzinfo=timezone.utc)
    end = datetime.combine(args.end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    params = {"host":os.environ["DJANGO_DB_HOST"],
              "port":int(os.environ.get("DJANGO_DB_PORT","5432")),
              "dbname":os.environ["DJANGO_DB_NAME"],
              "user":os.environ["DJANGO_DB_USER"],
              "password":os.environ["DJANGO_DB_PASSWORD"],
              "connect_timeout":5, "options":"-c default_transaction_read_only=on -c statement_timeout=120000"}
    # Rely on database-enforced SELECT-only role as well as read-only transaction.
    with psycopg.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("""
                SELECT timestamp, open, high, low, close
                FROM public.ea_bridge_marketbar
                WHERE source=%s AND symbol=%s AND timeframe='M15'
                  AND timestamp < %s AND timestamp >= %s
                ORDER BY timestamp
                LIMIT 16513
            """, (args.source, args.symbol, end, start - timedelta(days=90)))
            fetched = cur.fetchall()
    selected = [r for r in fetched if start <= r[0] < end]
    if not selected or len(fetched) >= 16513:
        raise RuntimeError("NO_SELECTED_BARS_OR_RANGE_TOO_LARGE: narrow dates to under 16000 candles")
    prior = [r for r in fetched if r[0] < start][-256:]
    selected = selected[:16000]
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
    baseline = _simulate(data, rows)[0]
    candidate = impulse_continuation_research(
        rows, start_ts=int(start.timestamp()), end_ts=int(end.timestamp()),
        balance=args.balance, spread_points=args.spread_points, point=args.point,
        tick_size=args.tick_size, tick_value=args.tick_value,
        volume_min=args.volume_min, volume_max=args.volume_max,
        volume_step=args.volume_step, risk_percent=args.risk_percent,
        slippage_points=args.slippage_points,
        commission_per_lot=args.commission_per_lot)
    def summarize(x):
        return {k:x.get(k) for k in ("signals","closed_trades","win_rate","net_gain",
                                     "profit_factor","max_drawdown_pct","open_position")}
    output = {
        "mode":"ISOLATED_READ_ONLY_LABORATORY", "trading_authorized":False,
        "market_source":args.source,"symbol":args.symbol,
        "start":args.start.isoformat(),"end":args.end.isoformat(),
        "data_quality":quality,
        "data_quality_warning":quality["gap_count"]>0 or any(quality["excluded"].values()),
        "assumptions":{k:data[k] for k in ("balance","spread_points","point",
            "tick_size","tick_value","volume_min","volume_max","volume_step",
            "slippage_points","commission_per_lot","risk_percent")},
        "trend_pullback":summarize(baseline),
        "impulse_continuation":summarize(candidate),
        "candidate_status":candidate.get("status"),
        "candidate_rejections":candidate.get("rejections"),
        "limitations":"Historical bid-only OHLC and modeled costs; not broker-verified; gaps may invalidate comparisons; cent-account units require broker verification. No live execution."
    }
    print(json.dumps(output,indent=2,sort_keys=True))


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print("LAB_ERROR:",str(exc),file=sys.stderr)
        sys.exit(1)
