import csv

from ramon.stop_feasibility import audit, load_m15


def test_only_complete_m15_bars_are_used(tmp_path):
    path = tmp_path / "m5.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("time", "open", "high", "low", "close", "spread_points"))
        for time in (0, 300, 600, 900, 1200, 1800, 2100, 2400):
            writer.writerow((time, 100, 101, 99, 100, 42))
    bars = load_m15(path)
    assert [bar.time for bar in bars] == [0, 1800]


def test_shorter_stops_increase_feasibility():
    # Continuous 15-minute candles. A 1.5 ATR stop is outside this cap,
    # while a 1.0 ATR stop fits.
    from ramon.stop_feasibility import Bar

    bars = [Bar(i * 900, 100, 101, 99, 100, 0) for i in range(27)]
    result = audit(bars, cap_usd=2.2, min_lot_usd_per_price=1,
                   factors=(1.5, 1.0))
    assert result["factors"]["1.5"]["eligible"] == 0
    assert result["factors"]["1.0"]["eligible"] > 0
    assert result["common_direction_samples"] == 0


def test_shorter_stop_is_more_easily_touched_on_same_opportunities():
    from ramon.stop_feasibility import Bar

    bars = [Bar(i * 900, 100, 101, 99, 100, 0) for i in range(21)]
    bars[15] = Bar(15 * 900, 100, 102.5, 97.5, 100, 0)
    result = audit(bars, cap_usd=3.1, min_lot_usd_per_price=1,
                   factors=(1.5, 1.0))
    assert result["common_direction_samples"] == 2
    assert result["factors"]["1.5"]["common_stop_hit_pct"] == 0
    assert result["factors"]["1.0"]["common_stop_hit_pct"] == 100
