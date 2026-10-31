#!/usr/bin/env python3
"""Synthetic XAUUSD market-data client for the Ramon Django EA bridge.

This tool sends telemetry and market events only. It is not an MT5 terminal,
does not connect to a broker, and cannot execute trades.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://127.0.0.1:8017"
DEFAULT_SYMBOL = "XAUUSD_o"
TIMEFRAME_SECONDS = 15 * 60
INSTANCE_ID = "RAMON-SIM-001"
ACCOUNT_LOGIN = "99999999"
SERVER_NAME = "RAMON-SIMULATOR"
EA_VERSION = "SIM-1.0.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding exported variables."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'").strip()
        if key and key not in os.environ:
            os.environ[key] = value


def post_json(base_url: str, token: str, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    request = Request(
        base_url.rstrip("/") + path,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ramon-market-simulator/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path}: HTTP {exc.code}: {detail[:500]}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"{path}: connection failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path}: Django returned invalid JSON") from exc


@dataclass
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def telemetry(self) -> dict[str, Any]:
        return {
            "timestamp": iso_utc(datetime.fromtimestamp(self.timestamp, timezone.utc)),
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "close": round(self.close, 2),
            "volume": round(self.volume, 2),
            "closed": True,
        }

    def decision(self) -> dict[str, Any]:
        return {
            "time": self.timestamp,
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "close": round(self.close, 2),
        }


class MarketSimulator:
    def __init__(self, symbol: str, start_price: float, seed: int, point: float, spread_points: float):
        self.symbol = symbol
        self.price = start_price
        self.point = point
        self.spread_points = spread_points
        self.rng = random.Random(seed)
        self.bars = self._historical_bars()
        self.last_bar_bucket = self.bars[-1].timestamp
        self.balance = 10_000.0
        self.equity = 10_000.0
        self.tick_number = 0

    def _historical_bars(self) -> list[Candle]:
        now_bucket = int(time.time() // TIMEFRAME_SECONDS) * TIMEFRAME_SECONDS
        last_closed = now_bucket - TIMEFRAME_SECONDS
        price = self.price
        result: list[Candle] = []
        # Create a reproducible but non-flat 128-candle warm-up history.
        for index in range(128):
            stamp = last_closed - (127 - index) * TIMEFRAME_SECONDS
            drift = self.rng.uniform(-0.75, 0.75)
            open_price = price
            close = max(100.0, open_price + drift)
            high = max(open_price, close) + self.rng.uniform(0.05, 0.55)
            low = min(open_price, close) - self.rng.uniform(0.05, 0.55)
            result.append(Candle(stamp, open_price, high, low, close, self.rng.uniform(20, 180)))
            price = close
        self.price = price
        return result

    def tick(self) -> tuple[float, float, float]:
        # A bounded Gaussian random walk with occasional volatility bursts.
        volatility = self.rng.choice((0.12, 0.18, 0.25, 0.45))
        change = self.rng.gauss(0.0, volatility)
        self.price = max(100.0, self.price + change)
        spread = self.spread_points * self.point * self.rng.uniform(0.8, 1.8)
        mid = round(self.price, 2)
        bid = round(mid - spread / 2, 2)
        ask = round(mid + spread / 2, 2)
        self.tick_number += 1
        return bid, ask, mid

    def close_new_bars_if_due(self) -> None:
        now_bucket = int(time.time() // TIMEFRAME_SECONDS) * TIMEFRAME_SECONDS
        while self.last_bar_bucket + TIMEFRAME_SECONDS <= now_bucket - TIMEFRAME_SECONDS:
            stamp = self.last_bar_bucket + TIMEFRAME_SECONDS
            previous = self.bars[-1].close
            close = max(100.0, previous + self.rng.gauss(0.0, 1.1))
            high = max(previous, close) + self.rng.uniform(0.05, 0.65)
            low = min(previous, close) - self.rng.uniform(0.05, 0.65)
            self.bars.append(Candle(stamp, previous, high, low, close, self.rng.uniform(30, 220)))
            self.bars = self.bars[-128:]
            self.last_bar_bucket = stamp
            self.price = close

    def telemetry_payload(self, bid: float, ask: float) -> dict[str, Any]:
        now = utc_now()
        return {
            "protocol_version": "ramon-ea-command-v1",
            "instance_id": INSTANCE_ID,
            "account_login": ACCOUNT_LOGIN,
            "server_name": SERVER_NAME,
            "symbol": self.symbol,
            "timeframe": "M15",
            "ea_version": EA_VERSION,
            "account_trade_mode": 0,
            "broker_day": now.date().isoformat(),
            "broker_time": iso_utc(now),
            "quote_time": iso_utc(now),
            "quote": {"bid": bid, "ask": ask, "point": self.point},
            "account": {
                "balance": round(self.balance, 2),
                "equity": round(self.equity, 2),
                "margin": 0.0,
                "free_margin": round(self.equity, 2),
                "currency": "USD",
                "leverage": 100,
            },
            "positions": [],
            "terminal_trade_allowed": False,
            "last_command_id": "",
            "bars": [bar.telemetry() for bar in self.bars],
        }

    def event_payload(self, bid: float, ask: float) -> dict[str, Any]:
        now = utc_now()
        return {
            "instance_id": INSTANCE_ID,
            "event_id": f"sim-tick-{uuid.uuid4().hex}",
            "event_type": "MARKET_TICK",
            "occurred_at": iso_utc(now),
            "symbol": self.symbol,
            "timeframe": "M15",
            "source": "SYNTHETIC_SIMULATOR",
            "sequence": self.tick_number,
            "bid": bid,
            "ask": ask,
            "spread_points": round((ask - bid) / self.point, 2),
            "mid": round((bid + ask) / 2, 2),
        }

    def decision_payload(self, bid: float, ask: float) -> dict[str, Any]:
        return {
            "instance_id": INSTANCE_ID,
            "account_login": ACCOUNT_LOGIN,
            "server_name": SERVER_NAME,
            "symbol": self.symbol,
            "timeframe": "M15",
            "bars": [bar.decision() for bar in self.bars],
            "bid": bid,
            "ask": ask,
            "point": self.point,
            "account_balance": self.balance,
            "risk_percent": 0.5,
            "point_value_per_lot": 1.0,
            "volume_step": 0.01,
            "min_volume": 0.01,
            "max_volume": 100.0,
            "stop_level_points": 50.0,
            "take_profit_points": 100.0,
            "daily_loss_percent": 0.0,
            "open_risk_percent": 0.0,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Send synthetic XAUUSD telemetry to Ramon Django (no trading).")
    parser.add_argument("--base-url", default=os.getenv("RAMON_SIM_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Read RAMON_EA_BEARER_TOKEN from this file")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--start-price", type=float, default=2700.0)
    parser.add_argument("--point", type=float, default=0.01)
    parser.add_argument("--spread-points", type=float, default=25.0)
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between synthetic ticks")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--once", action="store_true", help="Send one telemetry snapshot and one tick event, then exit")
    parser.add_argument("--decisions", action="store_true", help="Also call Django's advisory decision endpoint")
    args = parser.parse_args()

    if args.interval < 0.5 or args.timeout <= 0 or args.point <= 0 or args.spread_points <= 0:
        parser.error("interval must be >= 0.5 and timeout, point, spread must be positive")
    load_env_file(args.env_file)
    token = os.getenv("RAMON_EA_BEARER_TOKEN", "").strip()
    if not token:
        print("ERROR: RAMON_EA_BEARER_TOKEN is missing. Set it in .env or export it.", file=sys.stderr)
        return 2

    sim = MarketSimulator(args.symbol, args.start_price, args.seed, args.point, args.spread_points)
    print(f"Ramon simulator -> {args.base_url}")
    print(f"Source={INSTANCE_ID} | Symbol={args.symbol} | M15 bars={len(sim.bars)}")
    print("SAFETY: synthetic data only; terminal_trade_allowed=false; no commands are polled or executed.")
    print("Press Ctrl+C to stop.")

    last_decision_bar = None
    try:
        while True:
            sim.close_new_bars_if_due()
            bid, ask, _ = sim.tick()
            try:
                telemetry = post_json(
                    args.base_url, token, "/api/ea/v1/telemetry",
                    sim.telemetry_payload(bid, ask), args.timeout,
                )
                event = post_json(
                    args.base_url, token, "/api/ea/v1/events",
                    sim.event_payload(bid, ask), args.timeout,
                )
                decision_note = ""
                current_bar = sim.bars[-1].timestamp
                if args.decisions and current_bar != last_decision_bar:
                    result = post_json(
                        args.base_url, token, "/api/ea/v1/decision",
                        sim.decision_payload(bid, ask), args.timeout,
                    )
                    last_decision_bar = current_bar
                    decision_note = f" | decision={result.get('decision')} reason={result.get('reason')}"
                print(
                    f"[{iso_utc(utc_now())}] bid={bid:.2f} ask={ask:.2f} "
                    f"bars={telemetry.get('stored_bars')} event={event.get('accepted')}"
                    f"{decision_note}",
                    flush=True,
                )
            except RuntimeError as exc:
                print(f"ERROR: {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nSimulator stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
