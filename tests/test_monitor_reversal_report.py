import json
from datetime import datetime, timedelta, timezone

from ramon.monitor import daily_reversal_report


def test_missing_daily_reversal_report(tmp_path):
    result=daily_reversal_report(tmp_path/"ramon_history.sqlite3","XAUUSD_l")
    assert result["available"] is False


def test_read_only_daily_reversal_summary(tmp_path):
    db=tmp_path/"ramon_history.sqlite3"
    folder=tmp_path/"reports"/"reversal"
    folder.mkdir(parents=True)
    now=datetime(2026,10,12,7,30,tzinfo=timezone.utc)
    report={"generated_utc":(now-timedelta(minutes=10)).isoformat(),
            "symbol":"XAUUSD_l","execution_enabled":False,
            "data_quality":{"M1":{"status":"READY","bars":1500,"age_seconds":80}},
            "reversal_audit":{"status":"INSUFFICIENT_INDEPENDENT_DAYS",
                "independent_utc_days":3,"required_independent_days":5,
                "overall":{"trades":110,"mean_net_price_move":-0.367},
                "folds":[{"fold":1,"stats":{"trades":28,"win_rate":0.28}}],
                "promotion_ready":False}}
    (folder/"latest.json").write_text(json.dumps(report))
    result=daily_reversal_report(db,"XAUUSD_l",now)
    assert result["available"] is True
    assert result["report_stale"] is False
    assert result["independent_utc_days"]==3
    assert result["folds"][0]["stats"]["trades"]==28
    assert result["promotion_ready"] is False
    assert result["execution_enabled"] is False
