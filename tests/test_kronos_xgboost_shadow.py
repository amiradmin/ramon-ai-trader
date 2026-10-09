"""Unit tests for offline shadow dataset boundaries and decisions."""
from ramon.kronos_direction_shadow import acceptable_gap, direction, pairs, summarize
from ramon.xgboost_timing_shadow import build_examples

def test_direction_abstains_in_neutral_band():
    assert direction(100.05, 100.0, 0.1) == "WAIT"
    assert direction(101, 100, 0.1) == "BUY"
    assert direction(99, 100, 0.1) == "SELL"

def test_kronos_accepts_market_closure_but_rejects_bad_gap():
    # Friday -> Sunday style closure is valid when actual timestamps are used.
    friday = 4 * 86400
    assert acceptable_gap(friday, friday + 2 * 86400)

    # A long mid-week hole is treated as missing/corrupt history.
    wednesday = 2 * 86400
    assert not acceptable_gap(wednesday, wednesday + 10 * 3600)

    bars=[(i*900,100,101,99,100) for i in range(30)]
    assert list(pairs(bars,lookback=8,horizon=3,stride=1))
    assert summarize([])["status"]=="insufficient_contiguous_bars"

def test_timing_uses_only_prior_features_and_future_labels():
    bars=[]
    for i in range(90):
        p=100.0+i*0.10
        bars.append((i*60,p,p+0.11,p-0.11,p+0.05))
    sample=build_examples(bars,horizon=5,stride=5,stop=.25,target=.25,spread=.02)
    assert sample
    assert all(len(features)==7 and label in (0,1) for _,features,label in sample)

def test_timing_rejects_gapped_input():
    bars=[(i*60,100,101,99,100) for i in range(90)]
    bars[30]=(bars[30][0]+86400,100,101,99,100)
    examples=build_examples(bars,horizon=5,stride=5)
    assert all(not (10*60 <= t <= 35*60) for t,_,_ in examples)

def test_timing_supports_5_minute_candles():
    bars=[]
    for i in range(90):
        p=100+i*0.1
        bars.append((i*300,p,p+0.5,p-0.5,p+0.1))
    # Use reachable TP/SL distances; the default 2.0 units are outside the
    # synthetic three-candle window and would legitimately yield no labels.
    kwargs=dict(horizon=3,stride=5,stop=0.2,target=0.2,spread=0.02)
    assert build_examples(bars,bar_seconds=300,**kwargs)
    assert not build_examples(bars,bar_seconds=60,**kwargs)
