"""Offline M1 continuation research; never an execution signal or tick backtest."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from collections import Counter


@dataclass(frozen=True)
class Minute:
    time: datetime  # MT5 broker wall time; deliberately not labelled UTC
    open: float
    high: float
    low: float
    close: float
    spread: float


def load_minutes(path: Path, point: float = .01) -> list[Minute]:
    if not math.isfinite(point) or point <= 0:
        raise ValueError("invalid point")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = [Minute(datetime.strptime(r['<DATE>']+' '+r['<TIME>'], '%Y.%m.%d %H:%M:%S'),
                       *(float(r['<'+k+'>']) for k in ['OPEN', 'HIGH', 'LOW', 'CLOSE']),
                       float(r['<SPREAD>'])*point) for r in reader]
    validate(rows)
    return rows


def validate(rows: list[Minute]) -> None:
    if not rows:
        raise ValueError("empty history")
    for i, b in enumerate(rows):
        if (not all(math.isfinite(v) for v in [b.open, b.high, b.low, b.close, b.spread])
                or not 0 < b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high
                or b.spread <= 0 or b.time.second or b.time.microsecond
                or (i and b.time <= rows[i-1].time)):
            raise ValueError(f"invalid/duplicate/nonascending bar {i}")


def bucket(t: datetime, minutes: int = 15) -> datetime:
    return t.replace(minute=t.minute//minutes*minutes, second=0, microsecond=0)


def aggregates(rows: list[Minute], period: int) -> dict[datetime, dict]:
    result: dict[datetime, dict] = {}
    for b in rows:
        k = bucket(b.time, period)
        if k not in result:
            result[k] = dict(open=b.open, high=b.high, low=b.low, close=b.close, count=1)
        else:
            z = result[k]
            z.update(high=max(z['high'], b.high), low=min(z['low'], b.low), close=b.close,
                     count=z['count']+1)
    return result


def candidate(rows: list[Minute], i: int, five: dict, fifteen: dict, method: str) -> tuple[int, float] | None:
    """Completed-minute proxy of v064 direction, with/without two corrective closes.

    Reference ATR is mean TR of 14 complete prior M15 bars, not server LastAtr.
    Current aggregate HIGH/LOW/CLOSE are never accessed (would leak the future).
    """
    if method not in {'two_bar_pullback', 'continuation_break'}:
        raise ValueError("unknown method")
    if i < 4 or rows[i].time-rows[i-3].time != timedelta(minutes=3):
        return None
    b, p, pp, ppp = rows[i], rows[i-1], rows[i-2], rows[i-3]
    k5, k15 = bucket(b.time, 5), bucket(b.time)
    f, a = five.get(k5), fifteen.get(k15)
    pf, pa = five.get(k5-timedelta(minutes=5)), fifteen.get(k15-timedelta(minutes=15))
    prior = [fifteen.get(k15-timedelta(minutes=15*n)) for n in range(15, 0, -1)]
    if (not f or not a or not pf or not pa or pf['count'] != 5
            or any(not z or z['count'] != 15 for z in prior)):
        return None
    atr = sum(max(z['high']-z['low'], abs(z['high']-prev['close']),
                  abs(z['low']-prev['close'])) for prev, z in zip(prior, prior[1:]))/14
    if atr <= 0:
        return None
    up = b.close > max(f['open'], a['open'])+.05*atr
    down = b.close+b.spread < min(f['open'], a['open'])-.05*atr
    up &= b.close > max(pf['high'], pa['high'])+.05*atr or b.close > max(f['open'], a['open'])+.15*atr
    down &= b.close+b.spread < min(pf['low'], pa['low'])-.05*atr or b.close+b.spread < min(f['open'], a['open'])-.15*atr
    for d, trend in [(1, up), (-1, down)]:
        price = b.close+(b.spread if d == -1 else 0)
        trigger = p.high if d == 1 else p.low
        corrective = pp.close < ppp.close and p.close < pp.close if d == 1 else pp.close > ppp.close and p.close > pp.close
        if (trend and d*(price-b.open) > .03*atr and d*(price-trigger) > .03*atr
                and d*(price-trigger) <= .25*atr
                and (method == 'continuation_break' or corrective)):
            # Same 3-minute structural anchor for both alternatives, no risk-fitting.
            anchor = min(b.low, p.low, pp.low) if d == 1 else max(b.high, p.high, pp.high)
            return d, anchor-d*.01
    return None


def walk_exit(rows: list[Minute], start: int, direction: int, entry: float, stop: float,
              risk: float, path: str, slippage: float, protection: bool) -> tuple[int, float, str]:
    if path not in {'high_first', 'low_first'}:
        raise ValueError("unknown OHLC path")
    target_profit = 1.5*risk
    stop_profit = direction*(stop-entry)
    expiry = bucket(rows[start].time)+timedelta(minutes=45)
    peak = 0.0
    last_profit = 0.0
    for j in range(start, len(rows)):
        b = rows[j]
        prices = [b.open, b.high, b.low, b.close] if path == 'high_first' else [b.open, b.low, b.high, b.close]
        profits = [direction*(p+(b.spread if direction == -1 else 0)-entry) for p in prices]
        if j > start and (b.time-rows[j-1].time != timedelta(minutes=1) or b.time >= expiry):
            return j, profits[0]-slippage, 'gap_or_time'
        for n, profit in enumerate(profits):
            floor = max(stop_profit, peak-.25*risk) if protection and peak >= risk else stop_profit
            if profit <= floor:
                # Gapped opens fill at open; continuous segments cross the threshold.
                fill = profit if n == 0 else floor
                return j, fill-slippage, 'protection' if floor > stop_profit else 'sl'
            if profit >= target_profit:
                return j, target_profit-slippage, 'tp'
            peak = max(peak, profit)
            last_profit = profit
    return len(rows)-1, last_profit-slippage, 'file_end'


def screen(rows: list[Minute], method: str, path: str, slippage: float = .02,
           protection: bool = True, commission: float = 0.0) -> tuple[list[dict], dict]:
    if any(not math.isfinite(x) or x < 0 for x in [slippage, commission]):
        raise ValueError("invalid costs")
    five, fifteen = aggregates(rows, 5), aggregates(rows, 15)
    trades: list[dict] = []
    blocked: Counter = Counter()
    i, last_bucket = 4, None
    while i+1 < len(rows):
        signal = candidate(rows, i, five, fifteen, method)
        if not signal:
            i += 1
            continue
        d, stop = signal
        b = rows[i+1]
        rejection = None
        if b.time-rows[i].time != timedelta(minutes=1):
            rejection = 'next_minute_missing'
        elif bucket(b.time) == last_bucket:
            rejection = 'already_entered_this_m15'
        elif b.spread > .50:
            rejection = 'spread'
        entry = b.open+(b.spread if d == 1 else 0)+d*slippage
        risk = d*(entry-stop)+.30  # configured deviation reserve; price units
        if rejection is None:
            if d*(entry-stop) <= 0 or not 0 < risk <= 4:
                rejection = 'structural_risk'
            elif b.spread > risk*.25:
                rejection = 'spread_risk_ratio'
        if rejection:
            blocked[rejection] += 1
            i += 1
            continue
        j, pnl, reason = walk_exit(rows, i+1, d, entry, stop, risk, path, slippage, protection)
        trades.append(dict(entry_time=b.time.isoformat(), exit_time=rows[j].time.isoformat(),
                           direction=d, entry=entry, stop=stop, reserved_risk=risk,
                           pnl_price_units=pnl-commission, reason=reason))
        last_bucket = bucket(b.time)
        i = j+1
    return trades, dict(blocked)


def summarize(trades: list[dict]) -> dict:
    gains = sum(max(x['pnl_price_units'], 0) for x in trades)
    losses = -sum(min(x['pnl_price_units'], 0) for x in trades)
    equity = peak = drawdown = 0.0
    for x in trades:
        equity += x['pnl_price_units']
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
    return dict(trades=len(trades), wins=sum(x['pnl_price_units'] > 0 for x in trades),
                pnl_price_units=round(equity, 5), profit_factor=round(gains/losses, 4) if losses else None,
                drawdown_price_units=round(drawdown, 5), exits=dict(Counter(x['reason'] for x in trades)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--holdout-start', required=True, type=datetime.fromisoformat)
    parser.add_argument('--holdout-end', required=True, type=datetime.fromisoformat)
    parser.add_argument('--commission-price-units', type=float, default=0.0)
    args = parser.parse_args()
    rows = load_minutes(args.csv)
    # Export may include a forming last minute; exclude it unconditionally.
    rows = rows[:-1]
    if not rows or not rows[0].time < args.holdout_start < args.holdout_end <= rows[-1].time:
        raise SystemExit('invalid chronological holdout boundaries')
    result = dict(source_sha256=hashlib.sha256(args.csv.read_bytes()).hexdigest(), bars=len(rows),
                  first=rows[0].time.isoformat(), last=rows[-1].time.isoformat(), timezone='broker_wall_time',
                  live_ready=False, classification='M1_PROXY_ONLY_NOT_EA_BACKTEST',
                  limitations=['No real ticks; two fictional OHLC paths, not guaranteed execution bounds',
                               'No model/news/priority gates; no independent quote confirmations',
                               'Unknown historical stops level/margin; assumes 1 account unit per price at minimum lot',
                               'Protection assumes instantaneous crossing fills, not the EA timer',
                               'Per-minute spread is constant; commission defaults to unknown/unpriced zero'],
                  assumptions=dict(point=.01, max_spread=.50, risk_cap_price_units=4,
                                   deviation_reserve=.30, tp_r=1.5, protection_activation_r=1,
                                   protection_giveback_r=.25, commission_price_units=args.commission_price_units),
                  holdout_start=args.holdout_start.isoformat(), holdout_end=args.holdout_end.isoformat(), results={})
    for method in ['two_bar_pullback', 'continuation_break']:
        for path in ['high_first', 'low_first']:
            for protection in [False, True]:
                for slip in [.02, .10]:
                    trades, blocked = screen(rows, method, path, slip, protection, args.commission_price_units)
                    # Never score file-end forced exits or trades straddling split boundaries.
                    train = [x for x in trades if x['exit_time'] < args.holdout_start.isoformat() and x['reason'] != 'file_end']
                    test = [x for x in trades if x['entry_time'] >= args.holdout_start.isoformat()
                            and x['exit_time'] < args.holdout_end.isoformat() and x['reason'] != 'file_end']
                    key = f'{method}/{path}/protection={protection}/slippage={slip}'
                    result['results'][key] = dict(train=summarize(train), holdout=summarize(test), blocked=blocked)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
