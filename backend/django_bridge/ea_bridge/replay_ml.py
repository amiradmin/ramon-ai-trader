"""Research-only feature extraction from historical M15 replay trades.

Never combine simulated replay fills with broker-confirmed LearningTradeOutcome.
All market features stop at the completed candle when the signal was emitted.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


def make_features(bars, *, signal_at, setup, side, spread_points):
    """Deterministic pre-entry features. bars must be ordered oldest to newest."""
    if side not in {"BUY", "SELL"}:
        raise ValueError("Invalid trade direction")
    if not isinstance(signal_at, int):
        raise ValueError("Signal timestamp must be integer UTC epoch seconds")
    history = [b for b in bars if int(b["time"]) <= signal_at]
    if len(history) < 60 or int(history[-1]["time"]) != signal_at:
        raise ValueError("Missing exact completed signal-time M15 candle")
    if signal_at % 900:
        raise ValueError("Signal candle off M15 UTC grid")
    h = history[-60:]
    closes = [float(b["close"]) for b in h]
    tr = [max(float(h[i]["high"])-float(h[i]["low"]),
              abs(float(h[i]["high"])-closes[i-1]),
              abs(float(h[i]["low"])-closes[i-1])) for i in range(1, len(h))]
    atr = sum(tr[-14:]) / 14
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("Invalid historical ATR")
    def ema(series, period):
        alpha = 2 / (period + 1)
        value = series[0]
        for price in series[1:]:
            value = alpha * price + (1-alpha) * value
        return value
    ema20, ema50 = ema(closes, 20), ema(closes, 50)
    change = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    up = sum(max(0, x) for x in change[-14:]) / 14
    down = sum(max(0, -x) for x in change[-14:]) / 14
    rsi = 100.0 if down == 0 else 100 - 100 / (1 + up/down)
    result = {
        "tag_side": side,
        "tag_setup_type": str(setup),
        "tag_strategy_profile": str(setup),
        "snapshot_atr": atr,
        "snapshot_rsi": rsi,
        "snapshot_ema_distance": (ema20-ema50) / atr,
        "snapshot_spread_points": float(spread_points),
        "snapshot_return_1_atr": (closes[-1]-closes[-2])/atr,
        "snapshot_return_5_atr": (closes[-1]-closes[-6])/atr,
        "snapshot_bar_range_atr": (float(h[-1]["high"])-float(h[-1]["low"]))/atr,
    }
    if not all(math.isfinite(v) for v in result.values() if isinstance(v, float)):
        raise ValueError("Non-finite features")
    return result


def replay_training_rows(report, market_bars):
    """Return time-ordered (signal timestamp, feature dict, R multiple).

    This is not realized broker PnL. Replay ignores historical slippage/fees,
    and only trades closed by the simulation are labeled.
    """
    assumptions = report["assumptions"]
    data_quality = report["data_quality"]
    if not data_quality.get("history_fingerprint"):
        raise ValueError("Historical replay fingerprint missing")
    if data_quality.get("gap_count", 0) or any((data_quality.get("excluded") or {}).values()):
        raise ValueError("Replay has M15 gaps or excluded timestamps; fix source before ML training")
    spread = float(assumptions["spread_points"])
    if not math.isfinite(spread) or spread < 0:
        raise ValueError("Invalid spread assumption")
    fingerprint_rows = []
    import hashlib
    digest = hashlib.sha256()
    for bar in market_bars:
        digest.update(("{time}:{open:.8f}:{high:.8f}:{low:.8f}:{close:.8f};".format(
            time=int(bar["time"]), open=float(bar["open"]), high=float(bar["high"]),
            low=float(bar["low"]), close=float(bar["close"]))).encode())
    # The replay fingerprint includes warmup candles; the caller must supply the
    # exact accepted sequence from the selected job.
    if digest.hexdigest() != data_quality["history_fingerprint"]:
        raise ValueError("Replay market-history fingerprint mismatch")
    for strategy in report.get("strategies", []):
        for trade in strategy.get("trades", []):
            if trade.get("reason") not in ("STOP", "TARGET"):
                continue
            signal = int(trade["signal_at"])
            stop = float(trade["stop"])
            entry = float(trade["entry"])
            exit_price = float(trade["exit"])
            if not all(map(math.isfinite, (stop, entry, exit_price))) or abs(entry-stop) <= 0:
                continue
            sign = 1 if trade["side"] == "BUY" else -1
            multiple = (exit_price-entry)*sign/abs(entry-stop)
            features = make_features(market_bars, signal_at=signal,
                                     setup=strategy["setup"], side=trade["side"],
                                     spread_points=spread)
            fingerprint_rows.append((signal, features, multiple))
    fingerprint_rows.sort(key=lambda item: item[0])
    return fingerprint_rows
