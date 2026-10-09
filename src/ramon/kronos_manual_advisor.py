"""Optional CPU worker publishing one real Kronos forecast per completed M15 bar.

Run separately from the trading service; no order queue or live policy imports.
Requires official upstream Kronos dependencies in an isolated environment.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

from .kronos_direction_shadow import acceptable_gap, fetch_bars


def predict_latest(rows, predictor, *, symbol, lookback=256, horizon=4, neutral_band=.42, now=None):
    import pandas as pd
    now = time.time() if now is None else now
    history = [r for r in rows if r[0] + 900 <= now][-lookback:]
    if len(history) < lookback or int(history[-1][0]) != int(now // 900) * 900 - 900:
        raise ValueError("insufficient fresh completed M15 history")
    if any(not acceptable_gap(a[0], b[0]) for a, b in zip(history, history[1:])):
        raise ValueError("history contains a data gap")
    for row in history:
        _, op, hi, lo, cl = row
        if not all(math.isfinite(v) and v > 0 for v in (op, hi, lo, cl)) or not lo <= min(op, cl) <= max(op, cl) <= hi:
            raise ValueError("invalid OHLC history")
    frame = pd.DataFrame([r[1:] for r in history], columns=["open", "high", "low", "close"])
    stamp = pd.Series(pd.to_datetime([r[0] for r in history], unit="s", utc=True).tz_localize(None))
    bar = int(history[-1][0])
    future = pd.Series(pd.to_datetime([bar + 900 * i for i in range(1, horizon + 1)], unit="s", utc=True).tz_localize(None))
    forecast = predictor.predict(df=frame, x_timestamp=stamp, y_timestamp=future,
                                 pred_len=horizon, T=1.0, top_p=.9, sample_count=3, verbose=False)
    price = float(forecast["close"].iloc[-1])
    if not math.isfinite(price) or price <= 0:
        raise ValueError("invalid model output")
    return {"mode": "DISPLAY_ONLY", "model": "NeoQuasar/Kronos-small", "symbol": symbol,
            "signal_bar_time": bar, "generated": time.time(), "horizon_bars": horizon,
            "forecast_price": price, "reference_price": float(history[-1][4]), "neutral_band": neutral_band}


def publish(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as out:
        json.dump(payload, out, allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--kronos-source", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--lookback", type=int, default=256)
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--neutral-band", type=float, default=.42, help="Price units; set to current spread or larger")
    parser.add_argument("--output")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    if not 2 <= args.lookback <= 512 or not 1 <= args.horizon <= 16 or not math.isfinite(args.neutral_band) or args.neutral_band < 0:
        parser.error("invalid lookback/horizon/neutral band")
    output = Path(args.output) if args.output else Path(args.db).with_name("kronos_live_advice.json")
    sys.path.insert(0, str(Path(args.kronos_source).resolve()))
    import torch
    torch.set_num_threads(2)
    torch.manual_seed(42)
    from model import Kronos, KronosTokenizer, KronosPredictor
    print("Loading Kronos-small for display-only advice…", flush=True)
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=512)
    last_bar = None
    while True:
        try:
            rows = fetch_bars(args.db, args.symbol, args.lookback + 16)
            completed = [r for r in rows if r[0] + 900 <= time.time()]
            current = completed[-1][0] if completed else None
            if current != last_bar or not output.exists():
                report = predict_latest(rows, predictor, symbol=args.symbol, lookback=args.lookback,
                                        horizon=args.horizon, neutral_band=args.neutral_band)
                publish(output, report)
                last_bar = current
                print(json.dumps(report), flush=True)
        except Exception as exc:
            last_bar = None
            # Invalidate prior forecasts on errors, rather than republishing stale success.
            publish(output, {"mode": "DISPLAY_ONLY", "model": "NeoQuasar/Kronos-small", "symbol": args.symbol,
                             "status": "ERROR", "generated": time.time(), "error": str(exc)[:300]})
            print("Kronos unavailable:", exc, flush=True)
            if not args.watch:
                raise
        if not args.watch:
            return
        time.sleep(5)


if __name__ == "__main__":
    main()
