from ramon.mfe_mae_audit import _classify, _path_metrics, summarize


def test_buy_path_metrics_and_sequence():
    rows=[
        (0,100.2,99.8),
        (900,100.8,99.6),
        (1800,101.2,100.1),
    ]
    m=_path_metrics(rows,direction="BUY",entry=100.0,stop_distance=1.0)
    assert round(m["mfe_r"],3)==1.2
    assert round(m["mae_r"],3)==0.4
    assert m["sequence_0_5r"]=="FAVORABLE_FIRST"


def test_sell_path_metrics_and_same_bar_ambiguity():
    rows=[(0,100.7,99.3)]
    m=_path_metrics(rows,direction="SELL",entry=100.0,stop_distance=1.0)
    assert round(m["mfe_r"],3)==0.7
    assert round(m["mae_r"],3)==0.7
    assert m["sequence_0_5r"]=="SAME_M15_BAR_AMBIGUOUS"


def test_diagnostic_buckets():
    assert _classify({"mfe_r":.1,"mae_r":.8,"net_r":-1,"sequence_0_5r":"ADVERSE_FIRST"})=="DIRECTION_WEAK"
    assert _classify({"mfe_r":.8,"mae_r":.8,"net_r":-1,"sequence_0_5r":"ADVERSE_FIRST"})=="TIMING_STRESS"
    assert _classify({"mfe_r":.8,"mae_r":.2,"net_r":-.2,"sequence_0_5r":"FAVORABLE_FIRST"})=="EXIT_LEFT_EDGE"


def test_summary_counts_losses_that_had_favorable_excursion():
    rows=[
        {"diagnostic_bucket":"EXIT_LEFT_EDGE","sequence_0_5r":"FAVORABLE_FIRST","net_r":-.2,"mfe_r":.8,"mae_r":.2},
        {"diagnostic_bucket":"DIRECTION_WEAK","sequence_0_5r":"ADVERSE_FIRST","net_r":-1.0,"mfe_r":.1,"mae_r":1.1},
        {"diagnostic_bucket":"MIXED_OR_OK","sequence_0_5r":"FAVORABLE_FIRST","net_r":.7,"mfe_r":1.2,"mae_r":.2},
    ]
    s=summarize(rows,{"total_trades":3,"missing_entry":0,"missing_stop":0,"missing_bars":0})
    assert s["excursion"]["losses_with_mfe_ge_0_5r"]==1
    assert s["excursion"]["trades_with_mae_ge_1r"]==1
    assert s["coverage"]["coverage_rate"]==1
