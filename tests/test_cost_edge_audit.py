import random
import sqlite3

from ramon import cost_edge_audit as ca


def _db(tmp_path, bars):
    path = tmp_path / "h.sqlite3"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE history_bars (symbol TEXT, timeframe TEXT, time INTEGER, open REAL,"
                  " high REAL, low REAL, close REAL, spread_points INTEGER)")
        c.executemany("INSERT INTO history_bars VALUES ('XAUUSD_l','M15',?,?,?,?,?,?)", bars)
    return path


def _walk(n, spread, gap_at=None, seed=1):
    rnd, price, out = random.Random(seed), 2000.0, []
    t = 1_700_000_000 - (1_700_000_000 % 900)
    for i in range(n):
        if gap_at is not None and i == gap_at:
            t += 900 * 50
        o = price
        price += rnd.gauss(0, 1.0)
        out.append((t, o, max(o, price) + 0.3, min(o, price) - 0.3, price, spread))
        t += 900
    return out


def test_breakeven_rises_with_cost(tmp_path):
    rows = ca._load(_db(tmp_path, _walk(1500, 20)), "XAUUSD_l")
    low = ca.audit(ca.collect(rows, horizon=4, point=0.01, fallback_spread_points=0,
                              extra_cost_price=0.0), stop_atr=1.5)
    high = ca.audit(ca.collect(rows, horizon=4, point=0.01, fallback_spread_points=0,
                               extra_cost_price=0.5), stop_atr=1.5)
    assert high["overall"]["breakeven_accuracy"] > low["overall"]["breakeven_accuracy"] > 0.5


def test_gap_windows_are_skipped(tmp_path):
    rows = ca._load(_db(tmp_path, _walk(400, 20, gap_at=200)), "XAUUSD_l")
    samples = ca.collect(rows, horizon=4, point=0.01, fallback_spread_points=0, extra_cost_price=0)
    # 14 ATR bars + 4 horizon bars straddling the gap are excluded on each side.
    assert len(samples) < 400 - 14 - 4
    assert all(s["move"] >= 0 for s in samples)


def test_unknown_spread_uses_fallback_and_is_flagged(tmp_path):
    rows = ca._load(_db(tmp_path, _walk(600, 0)), "XAUUSD_l")
    rep = ca.audit(ca.collect(rows, horizon=4, point=0.01, fallback_spread_points=42,
                              extra_cost_price=0), stop_atr=1.5)
    assert rep["spread_known_fraction"] == 0.0
    assert abs(rep["overall"]["mean_cost"] - 0.42) < 1e-9


def test_too_few_samples(tmp_path):
    rows = ca._load(_db(tmp_path, _walk(50, 20)), "XAUUSD_l")
    assert ca.audit(ca.collect(rows, horizon=4, point=0.01, fallback_spread_points=0,
                               extra_cost_price=0), stop_atr=1.5)["status"] == "insufficient_samples"
