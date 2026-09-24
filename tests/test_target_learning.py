from __future__ import annotations

from ramon.core import Bar, Market
from ramon.target_learning import build_target_structure


def _market(closes: list[float], *, bid: float, ask: float) -> Market:
    bars = []
    base = 1_900_000_000
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close
        bars.append(
            Bar(
                base + i * 900,
                open_,
                max(open_, close) + 0.2,
                min(open_, close) - 0.2,
                close,
            )
        )
    # Market requires at least 128 completed bars.
    pad = 128 - len(bars)
    prefix = [
        Bar(base - (pad - i) * 900, 100.0, 100.2, 99.8, 100.0)
        for i in range(pad)
    ]
    return Market("XAUUSD_l", "M15", bid, ask, 0.01, tuple(prefix + bars))


def test_buy_targets_retrace_last_down_impulse() -> None:
    market = _market([110, 108, 106, 104, 102, 100, 100.2], bid=100.2, ask=100.4)
    target = build_target_structure(
        market, direction="BUY", atr=2.0, target_distance=6.0, lookback=7
    )
    assert target.ready == 1
    assert target.method == "counter_impulse_retrace"
    assert target.tp1 < target.tp2 < target.tp3
    assert target.tp1 > market.ask
    assert target.impulse_atr >= 0.75


def test_sell_targets_retrace_last_up_impulse() -> None:
    market = _market([90, 92, 94, 96, 98, 100, 99.8], bid=99.6, ask=99.8)
    target = build_target_structure(
        market, direction="SELL", atr=2.0, target_distance=6.0, lookback=7
    )
    assert target.ready == 1
    assert target.tp1 > target.tp2 > target.tp3
    assert target.tp1 < market.bid


def test_weak_or_invalid_structure_falls_back_without_changing_legacy_target() -> None:
    market = _market([100, 100.1, 100.0, 100.1], bid=100.0, ask=100.4)
    target = build_target_structure(
        market, direction="BUY", atr=2.0, target_distance=6.0, lookback=4
    )
    assert target.ready == 0
    assert target.method == "legacy_fallback"
    assert target.tp2 == target.legacy_target
