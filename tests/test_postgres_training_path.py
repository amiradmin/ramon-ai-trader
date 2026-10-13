from __future__ import annotations
import json
import sqlite3
from unittest.mock import patch
from ramon.postgres_training import read_training_examples, DDL

class Cursor:
    def __init__(self): self.rows=[]
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def execute(self,sql,params): 
        if "training_decision_samples" in sql:
            self.rows=[({"sample_key":"abc","symbol":"XAUUSD_l","quote_time":100,"schema_version":3,"chronos_model":"x","regime_features":"{}","entry_features":"{}","news_features":"{}","meta_base_features":"{}","base_decision":"BUY","final_decision":"BUY","direction":"BUY"},)]
        else:
            self.rows=[({"trade_key":"123","sample_key":"abc","symbol":"XAUUSD_l","closed":130,"opened":110,"net_r":0.5,"direction":"BUY","exit_reason":"DEAL_REASON_TP","training_status":"LEARNABLE","entry_source":"AUTO_RAMON"},)]
    def fetchall(self): return self.rows
class Conn:
    def __enter__(self):return self
    def __exit__(self,*a): return False
    def cursor(self): return Cursor()

def test_training_postgres_adapter_retains_filters():
    import sys
    from types import SimpleNamespace
    with patch.dict(sys.modules,{"psycopg":SimpleNamespace(connect=lambda *a,**kw:Conn())}):
        rows=read_training_examples("private", "XAUUSD_l", "x")
    assert len(rows)==1
    assert rows[0].time==100
    assert rows[0].net_r==0.5

def test_schema_has_keys():
    assert "PRIMARY KEY" in DDL
    assert "training_trade_outcomes" in DDL
