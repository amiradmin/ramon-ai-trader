from __future__ import annotations
import json
import sqlite3
from unittest.mock import patch
from ramon.postgres_training import DDL, TRAINING_EXAMPLES_SQL, read_training_examples

class Cursor:
    def __init__(self): self.rows=[]
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def execute(self,sql,params=None):
        self.sql = sql
        self.params = params
        if "SELECT" in sql:
            self.rows=[(100,130,"{}","{}","{}","{}",0.5,"BUY","BUY","DEAL_REASON_TP")]
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


def test_native_training_query_matches_sqlite_selection_contract():
    assert "JOIN ramon_migration.training_trade_outcomes" in TRAINING_EXAMPLES_SQL
    assert "training_status' = 'LEARNABLE'" in TRAINING_EXAMPLES_SQL
    assert "DASHBOARD_OPPORTUNITY" in TRAINING_EXAMPLES_SQL
    assert "ORDER BY" in TRAINING_EXAMPLES_SQL
    assert "?" not in TRAINING_EXAMPLES_SQL

def test_schema_has_keys():
    assert "PRIMARY KEY" in DDL
    assert "training_trade_outcomes" in DDL


def test_same_decision_multiple_trades_sorted_by_trade_key():
    from ramon.train_roles import read_trade_examples
    db = sqlite3.connect(":memory:")
    db.executescript("""
    CREATE TABLE decision_samples (
      sample_key TEXT, symbol TEXT, quote_time INTEGER, regime_features TEXT,
      entry_features TEXT, news_features TEXT, meta_base_features TEXT,
      base_decision TEXT, direction TEXT, chronos_model TEXT,
      schema_version INTEGER, final_decision TEXT
    );
    CREATE TABLE trade_outcomes (
      trade_key TEXT, sample_key TEXT, symbol TEXT, closed INTEGER,
      net_r REAL, direction TEXT, exit_reason TEXT, training_status TEXT,
      entry_source TEXT, opened INTEGER
    );
    """)
    db.execute("INSERT INTO decision_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
      ("shared","XAUUSD_l",100,"{}","{}","{}","{}","BUY","BUY","x",3,"BUY"))
    for trade_key, closed in (("z",150),("a",130)):
        db.execute("INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
          (trade_key,"shared","XAUUSD_l",closed,1.0,"BUY","TP","LEARNABLE","AUTO_RAMON",110))
    rows = read_trade_examples(db,"XAUUSD_l","x")
    assert [row.label_end for row in rows] == [130,150]
