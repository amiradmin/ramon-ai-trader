from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ramon.continuation_screen import Minute, aggregates, candidate, load_minutes, screen, walk_exit


T = datetime(2026, 9, 1, 10)


def minute(n, o=100, h=101, l=99, c=100, spread=.42):
    return Minute(T+timedelta(minutes=n), o, h, l, c, spread)


def test_validate_rejects_duplicate_and_nonfinite_export(tmp_path: Path):
    p = tmp_path/'bars.csv'
    header = '<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<SPREAD>\n'
    line = '2026.09.01\t10:00:00\t100\t101\t99\t100\t42\n'
    p.write_text(header+line+line)
    with pytest.raises(ValueError, match='duplicate'):
        load_minutes(p)
    p.write_text(header+line.replace('\t101\t', '\tnan\t'))
    with pytest.raises(ValueError, match='invalid'):
        load_minutes(p)


def test_profit_protection_depends_on_unknown_minute_path():
    rows = [minute(0, o=100, h=104, l=98, c=102)]
    high = walk_exit(rows, 0, 1, 100, 98, 2, 'high_first', .02, True)
    low = walk_exit(rows, 0, 1, 100, 98, 2, 'low_first', .02, True)
    assert high[1:] == (2.98, 'tp')
    assert low[1:] == (-2.02, 'sl')
    rows = [minute(0, o=100, h=102.4, l=99, c=100)]
    assert walk_exit(rows, 0, 1, 100, 98, 2, 'high_first', .02, True)[1] == pytest.approx(1.88)
    assert walk_exit(rows, 0, 1, 100, 98, 2, 'low_first', .02, False)[2] == 'file_end'


def test_sell_stop_uses_ask_and_gap_fills_worse_than_stop():
    rows = [minute(0, o=100, h=101.7, l=99, c=100)]
    assert walk_exit(rows, 0, -1, 100, 102, 2, 'high_first', .02, False)[1:] == (-2.02, 'sl')
    rows = [minute(0, o=97, h=98, l=96, c=97)]
    assert walk_exit(rows, 0, 1, 100, 98, 2, 'low_first', .02, False)[1:] == (-3.02, 'sl')


def test_expiry_counts_m15_boundaries_and_missing_minutes():
    rows = [minute(14, o=100, h=100.1, l=99.9), minute(45, o=100.5, h=100.5, l=100.5, c=100.5)]
    j, pnl, reason = walk_exit(rows, 0, 1, 100, 98, 2, 'high_first', .02, False)
    assert (j, reason) == (1, 'gap_or_time')
    assert pnl == pytest.approx(.48)


def series():
    rows = [minute(n, o=100, h=101, l=99, c=100) for n in range(300)]
    rows[241] = minute(241, o=100, h=101.2, l=100, c=101.2)
    return rows


def test_candidate_does_not_use_future_aggregate_high_or_close():
    rows = series()
    i = 241
    prefix = rows[:i+1]
    expected = candidate(prefix, i, aggregates(prefix, 5), aggregates(prefix, 15), 'continuation_break')
    assert expected is not None
    assert candidate(prefix, i, aggregates(prefix, 5), aggregates(prefix, 15), 'two_bar_pullback') is None
    rows[244] = minute(244, o=100, h=500, l=1, c=400)
    assert candidate(rows, i, aggregates(rows, 5), aggregates(rows, 15), 'continuation_break') == expected


def test_risk_cap_blocks_gapped_entry_instead_of_shrinking_stop():
    rows = series()
    rows[242] = minute(242, o=110, h=111, l=109, c=110)
    trades, blocked = screen(rows, 'continuation_break', 'high_first')
    assert blocked['structural_risk'] >= 1
    assert not any(x['entry_time'] == rows[242].time.isoformat() for x in trades)
    assert all(0 < x['reserved_risk'] <= 4 for x in trades)


def test_sell_candidate_uses_ask_breakout():
    rows = series()
    rows[241] = minute(241, o=100, h=100, l=98.5, c=98.5)
    found = candidate(rows, 241, aggregates(rows, 5), aggregates(rows, 15), 'continuation_break')
    assert found is not None and found[0] == -1
    rows[241] = minute(241, o=100, h=100, l=98.8, c=98.8)
    assert candidate(rows, 241, aggregates(rows, 5), aggregates(rows, 15), 'continuation_break') is None


def test_invalid_costs_fail_closed():
    with pytest.raises(ValueError, match='costs'):
        screen(series(), 'continuation_break', 'high_first', float('nan'))
