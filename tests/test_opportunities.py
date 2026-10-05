import json
import sqlite3
from ramon.opportunities import read_opportunities


def database(tmp_path):
    db=tmp_path/'history.db'
    with sqlite3.connect(db) as c:
        c.execute('''CREATE TABLE decision_samples(id INTEGER PRIMARY KEY,symbol TEXT,captured INTEGER,signal_bar_time INTEGER,sample_key TEXT,quote_time INTEGER,mid REAL,spread REAL,stop_distance REAL,target_distance REAL,final_decision TEXT,model_metadata TEXT)''')
        c.execute('CREATE TABLE trade_outcomes(sample_key TEXT,symbol TEXT)')
    return db


def add(db,index,*,side='BUY',bid=100,quote=None,bar=1000,edge=2,decision='WAIT',reason='trend_conflict'):
    base={'forecast_median':102,'buy_edge':edge if side=='BUY' else -2,'sell_edge':edge if side=='SELL' else -2,'minimum_edge':1,'signal_strength':.3,'stop_distance':2,'target_distance':4}
    metadata={'decision_audit':{'base':base,'final':{'reason':reason}}}
    with sqlite3.connect(db) as c:
        c.execute('INSERT INTO decision_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(index,'XAUUSD_l',10000+index,bar,str(index),quote or 20000+index*30,bid+.2,.4,2,4,decision,json.dumps(metadata)))


def test_duplicate_candidates_do_not_become_repeated_trades(tmp_path):
    db=database(tmp_path);add(db,1);add(db,2,decision='BUY',reason='forecast_up');add(db,3,bid=105)
    before=db.read_bytes();data=read_opportunities(db);assert db.read_bytes()==before
    assert len(data['opportunities'])==1
    row=data['opportunities'][0]
    assert row['sample_key']=='1' and row['model_approved'] and not row['executed']
    assert row['outcome']=='TP_OBSERVED'
    assert abs(row['entry']-100.4)<1e-8


def test_sell_outcomes_use_ask_and_never_impute_a_gap(tmp_path):
    db=database(tmp_path);add(db,1,side='SELL');add(db,2,side='SELL',bid=101.8)
    row=read_opportunities(db)['opportunities'][0]
    assert row['outcome']=='SL_OBSERVED'
    with sqlite3.connect(db) as c:c.execute('UPDATE decision_samples SET quote_time=21000 WHERE id=2')
    assert read_opportunities(db)['opportunities'][0]['outcome']=='DATA_GAP'


def test_nonpositive_edges_missing_sources_and_execution_evidence(tmp_path):
    absent=tmp_path/'absent.db';assert read_opportunities(absent)['opportunities']==[];assert not absent.exists()
    db=database(tmp_path);add(db,1,edge=-1);assert read_opportunities(db)['opportunities']==[]
    add(db,2)
    with sqlite3.connect(db) as c:c.execute('INSERT INTO trade_outcomes VALUES (?,?)',('2','XAUUSD_l'))
    assert read_opportunities(db)['opportunities'][0]['executed']
