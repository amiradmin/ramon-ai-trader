import csv
import sqlite3

from ramon.multitimeframe_dataset_builder import (
    build_dataset, export_dataset, FIELDS,
)


def candles():
    def series(interval):
        return {
            t: (t, 10.0, 11.0, 9.0, 10.5)
            for t in range(0, 7200, interval)
        }
    return {"M5": series(300), "M15": series(900), "H1": series(3600)}


def decision(id_, bar, captured, sample):
    return dict(id=id_, symbol="XAUUSD_l", sample_key=sample,
                signal_bar_time=bar, captured=captured,
                direction="BUY", base_decision="BUY")


def trade(sample, opened=3700, closed=4000, net_r=-0.5):
    return dict(symbol="XAUUSD_l", sample_key=sample,
                opened=opened, closed=closed, net_r=net_r, net_units=-5)


def test_earliest_snapshot_chosen_without_outcome_leakage():
    decisions = [
        decision(2, 2700, 3650, "late"),
        decision(1, 2700, 3600, "first"),
        decision(3, 3600, 4500, "untraded"),
    ]
    rows, summary = build_dataset(decisions, [trade("late"), trade("first")], candles())
    assert summary["unique_signal_bars_selected"] == 2
    assert len(rows) == 2
    assert rows[0]["sample_key"] == "first"
    assert rows[0]["outcome_status"] == "REALIZED_TRADE"
    assert rows[0]["net_r"] == -0.5
    assert rows[1]["outcome_status"] == "UNTRADED_OR_UNKNOWN"
    assert rows[1]["net_r"] == ""
    assert summary["counts"]["duplicate_snapshots_excluded"] == 1


def test_future_bars_missing_context_and_noncausal_labels():
    bars = candles()
    decisions = [
        decision(1, 0, 100, "too_early"),
        decision(2, 2700, 3600, "bad_trade"),
    ]
    rows, summary = build_dataset(
        decisions, [trade("bad_trade", opened=3500)], bars)
    assert len(rows) == 1
    assert rows[0]["outcome_status"] == "INCOMPLETE_OR_NONCAUSAL_TRADE"
    assert rows[0]["net_r"] == ""
    assert summary["counts"]["M5_missing_context"] == 1


def test_ambiguous_trade_not_labeled():
    rows, summary = build_dataset(
        [decision(1, 2700, 3600, "a")],
        [trade("a"), trade("a")], candles())
    assert rows[0]["outcome_status"] == "AMBIGUOUS_MULTIPLE_TRADES"
    assert rows[0]["net_r"] == ""
    assert summary["counts"]["ambiguous_outcomes"] == 1


def test_sqlite_read_only_and_output_csv(tmp_path):
    db = tmp_path / "history.db"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE history_bars(
            symbol TEXT,timeframe TEXT,time INTEGER,
            open REAL,high REAL,low REAL,close REAL)""")
        con.execute("""CREATE TABLE decision_samples(
            id INTEGER,sample_key TEXT,symbol TEXT,signal_bar_time INTEGER,
            captured INTEGER,direction TEXT,base_decision TEXT)""")
        con.execute("""CREATE TABLE trade_outcomes(
            sample_key TEXT,symbol TEXT,opened INTEGER,closed INTEGER,
            net_r REAL,net_units REAL)""")
        for tf, series in candles().items():
            if tf == "H1":
                continue
            con.executemany("INSERT INTO history_bars VALUES(?,?,?,?,?,?,?)",
                            [("XAUUSD_l", tf, *bar) for bar in series.values()])
        d = decision(1, 2700, 3600, "s1")
        con.execute("INSERT INTO decision_samples VALUES(?,?,?,?,?,?,?)",
                    tuple(d[k] for k in ("id","sample_key","symbol","signal_bar_time",
                                         "captured","direction","base_decision")))
        t = trade("s1")
        con.execute("INSERT INTO trade_outcomes VALUES(?,?,?,?,?,?)",
                    tuple(t[k] for k in ("sample_key","symbol","opened","closed","net_r","net_units")))
    h1 = tmp_path / "h1.csv"
    with h1.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time","open","high","low","close","source"])
        for t, o, h, l, c in candles()["H1"].values():
            writer.writerow([t,o,h,l,c,"M15_AGGREGATED_RESEARCH_ONLY"])
    before = db.read_bytes()
    output = tmp_path / "data.csv"
    report = tmp_path / "report.json"
    stats = export_dataset(db, h1, symbol="XAUUSD_l", output=output, report=report)
    assert stats["complete_context_rows"] == 1
    assert db.read_bytes() == before
    with output.open(newline="") as stream:
        recs = list(csv.DictReader(stream))
    assert len(recs) == 1
    assert set(recs[0]) == set(FIELDS)
    assert recs[0]["net_r"] == "-0.5"
