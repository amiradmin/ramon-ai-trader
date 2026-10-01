from pathlib import Path
import hashlib
import re

EA=Path(__file__).parents[1]/"mt5"/"Ramon.mq5"


def function(text,name):
    start=text.index(name+"(")
    at=text.index("{",start); level=1; pos=at+1
    # Source contract helper only; detailed lexical validation is separate.
    while level:
        level += (text[pos]=="{")-(text[pos]=="}");pos+=1
    return text[at:pos]


def test_mql_source_fingerprint_and_every_policy_input_is_recorded():
    text=EA.read_text()
    expected=re.search(r'^const string ResearchSourceSHA256 = "([a-f0-9]{64})";',text,re.M)[1]
    normalized=re.sub(r'^const string ResearchSourceSHA256 = .*;\n','',text,flags=re.M)
    assert hashlib.sha256(normalized.encode()).hexdigest()==expected
    config=function(text,"ResearchConfig")
    for name in re.findall(r'^input\s+\w+\s+(\w+)\s*=',text,re.M):
        assert name in config


def test_manual_deal_ownership_and_partial_manual_intervention_are_preserved():
    text=EA.read_text()
    ownership=function(text,"ResearchDealOwned")
    assert "HistorySelectByPosition(identifier)" in ownership
    assert "DEAL_ENTRY_IN" in ownership
    assert "DEAL_MAGIC" in ownership
    assert "HistoryDealSelect(deal);" in ownership
    assert 'partial_detail=="manual_dashboard_close"' in text
    assert "manual_intervention=true;" in text
    assert 'ResearchPositionClose(ticket,MaxDeviationPoints,"manual_dashboard_close")' in text


def test_tick_path_is_observational_and_upload_cursor_follows_ack():
    text=EA.read_text()
    tick=function(text,"OnTick")
    assert "ResearchQuote();" in tick
    assert "WebRequest" not in tick
    assert "Trade." not in tick
    flush=function(text,"ResearchFlush")
    assert flush.index("if(code!=200) return;") < flush.index("ResearchOutboxOffset=next;")
    assert "FileFlush(cursor)" in flush
    wrapper=function(text,"ResearchPositionClose")
    assert "bool submitted=Trade.PositionClose(ticket,deviation);" in wrapper
    assert "return submitted;" in wrapper

