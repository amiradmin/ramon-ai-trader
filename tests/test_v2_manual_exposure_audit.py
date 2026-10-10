import sqlite3

from ramon.v2_manual_exposure_audit import audit


def test_manual_and_ea_sources_overlap_and_are_reported_separately(tmp_path):
    db=tmp_path/"history.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE trade_outcomes (trade_key TEXT, symbol TEXT, opened INTEGER, closed INTEGER, net_units REAL, entry_source TEXT, entry_magic INTEGER)")
        con.executemany("INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?)",[
            ("manual-a","XAUUSD_l",100,300,-250,"MANUAL",0),
            ("ea-b","XAUUSD_l",200,400,20,"EA",26092212),
            ("unknown","XAUUSD_l",450,500,0,None,None),
            ("other","EURUSD",100,500,-100,"MANUAL",0),
        ])
    result=audit(db)
    assert result["classification"]=={"HUMAN_INITIATED_OR_ASSISTED":1,"AUTOMATED":1,"UNKNOWN_SOURCE":1}
    assert result["max_concurrent_closed_trade_records"]==2
    assert result["max_mixed_manual_and_ea_concurrent"]==2
    assert result["net_units_by_source"]["HUMAN_INITIATED_OR_ASSISTED"]==-250
    assert result["net_units_by_source"]["AUTOMATED"]==20
    assert not result["live_positions_included"]


def test_generator_has_both_risk_and_latch_harness(tmp_path):
    import importlib.util
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location("tester_gen",root/"scripts/generate_ramon_tester.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output=module.create_tester_source((root/"mt5/Ramon.mq5").read_text())
    assert "RAMON_TEST_RISK PASS" in output
    assert "RAMON_TEST_LOCK PASS" in output
    assert "V2Preflight(ORDER_TYPE_SELL,price,stop,min_volume,lock_reason)" in output
    assert "if(!(bool)MQLInfoInteger(MQL_TESTER))" in output


def test_magic_does_not_turn_dashboard_or_missing_source_into_auto(tmp_path):
    db=tmp_path/"history.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE trade_outcomes (trade_key TEXT, symbol TEXT, opened INTEGER, closed INTEGER, net_units REAL, entry_source TEXT, entry_magic INTEGER)")
        con.executemany("INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?)",[
            ("dashboard","XAUUSD_l",100,300,-250,"DASHBOARD_OPPORTUNITY",26092212),
            ("human","XAUUSD_l",120,310,-20,"HUMAN_ASSISTED",26092212),
            ("unknown","XAUUSD_l",125,350,-40,None,26092212),
            ("auto","XAUUSD_l",140,400,5,"AUTO_RAMON",26092212),
        ])
    result=audit(db)
    assert result["classification"]=={
        "HUMAN_INITIATED_OR_ASSISTED":2,
        "UNKNOWN_SOURCE":1, "AUTOMATED":1}
    assert result["net_units_by_entry_source"]["DASHBOARD_OPPORTUNITY"]==-250
    assert result["net_units_by_source"]["UNKNOWN_SOURCE"]==-40
    assert result["max_mixed_manual_and_ea_concurrent"]==4
