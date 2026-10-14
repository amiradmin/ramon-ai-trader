from ramon.core import Bar
from scripts.audit_structure_outcomes import classify


def candle(high,low):
    return Bar(1700000000,100,high,low,100)


def test_buy_tp_and_sl():
    assert classify("BUY",100,99,102,[candle(102.1,99.5)])[0]=="TP"
    assert classify("BUY",100,99,102,[candle(101.0,98.5)])[0]=="SL"


def test_both_levels_ambiguous():
    assert classify("BUY",100,99,102,[candle(103,98)])[0]=="AMBIGUOUS"
    assert classify("SELL",100,101,98,[candle(102,97)])[0]=="AMBIGUOUS"


def test_sell_and_timeout():
    assert classify("SELL",100,101,98,[candle(99,97.9)])[0]=="TP"
    assert classify("SELL",100,101,98,[candle(100.5,99)])[0]=="TIMEOUT"
