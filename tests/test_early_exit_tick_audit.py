import csv
from ramon.early_exit_tick_audit import _audit_file, audit


def test_tick_path_observational_not_pnl(tmp_path):
    p=tmp_path/"quotes.csv"
    with p.open("w", newline="") as f:
        writer=csv.writer(f)
        writer.writerow(["time_ms","bid","ask"])
        for ms,bid in [(660000,4200),(661000,4202),(662000,4199),(663000,4201)]:
            writer.writerow([ms,bid,bid+0.42])
    result=_audit_file(p,660000,663000,660000,"BUY")
    assert result["observed_path"]["max_favorable_price_move"]==2
    assert result["observed_path"]["max_adverse_price_move"]==1
    assert result["observed_path"]["reference_side"]=="bid"
    assert "hypothetical_net_units" not in result
    sell=_audit_file(p,660000,663000,660000,"SELL")
    assert sell["observed_path"]["reference_side"]=="ask"
    assert sell["observed_path"]["max_favorable_price_move"]==1


def test_missing_or_invalid_quotes_fail_closed(tmp_path):
    missing=_audit_file(tmp_path/"no.csv",1000,2000,1000,"BUY")
    assert missing["status"]=="TICK_FILE_MISSING"
    p=tmp_path/"bad.csv"
    p.write_text("time_ms,bid,ask\n1000,4200,4199\n")
    result=_audit_file(p,1000,2000,1000,"BUY")
    assert result["status"]=="NO_TICKS_IN_WINDOW"
    assert result["observed_path"] is None


def test_audit_keeps_execution_counterfactual_disabled(tmp_path, monkeypatch):
    from ramon import early_exit_tick_audit as mod
    monkeypatch.setattr(mod,"validate", lambda *a,**kw:{
        "available": True,
        "trades":[{"sample_key":"x","closed":660,"direction":"BUY","outcome":"NOT_EVALUATED"}],
    })
    result=audit(tmp_path/"ignored.sqlite3",tmp_path,lookahead_minutes=5)
    assert result["counterfactual_status"]=="NOT_EVALUATED"
    assert result["trades"][0]["tick_audit"]["status"]=="TICK_FILE_MISSING"
