from ramon.logistic_readiness_audit import summarize

def row(t, end, won, side="BUY"):
    return (t, end, "{}", "{}", "{}", "{}", float(won), "BUY", side, "DEAL_REASON_TP")

def test_below_minimum_is_not_promoted():
    report = summarize([row(i*100, i*100+10, i%2) for i in range(120)])
    assert report["state"] == "BELOW_500_SAMPLE_GATE"
    assert report["promotion_possible_now"] is False
    assert sum(x["count"] for x in report["windows"].values()) == 120

def test_purges_boundary_crossing_labels():
    rows = [row(i*100, i*100+10, i%2) for i in range(120)]
    rows[59] = row(5900, 6100, 1)
    report = summarize(rows)
    assert report["windows"]["base"]["count"] == 59
    assert report["windows"]["meta"]["count"] == 36
    assert report["windows"]["holdout"]["count"] == 24

def test_empty_and_low_sample_are_explicit():
    report = summarize([])
    assert report["eligible"] == 0
    assert report["state"] == "INSUFFICIENT_FOR_TEMPORAL_WINDOWS"
