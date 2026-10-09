from ramon.entry_failure_audit import (
    _categorical_associations,
    _numeric_associations,
    flatten_scalars,
    summarize,
)


def test_flatten_scalars_nested_payload():
    raw={"decision_audit":{"intrabar_confirmed":0,"ai_trend":{"confirmed":1}},"regime":"RANGE"}
    out=flatten_scalars(raw,"model_metadata")
    assert out["model_metadata.decision_audit.intrabar_confirmed"] == 0
    assert out["model_metadata.decision_audit.ai_trend.confirmed"] == 1
    assert out["model_metadata.regime"] == "RANGE"


def _rows():
    rows=[]
    for i in range(60):
        weak=i<20
        adverse=i<25
        rows.append({
            "net_r": -1.0 if weak else 0.4,
            "labels":{
                "direction_weak":weak,
                "adverse_first":adverse,
                "exit_left_edge":False,
                "timing_stress":False,
                "loss":weak,
            },
            "features":{
                "entry_features.edge_ratio": 0.2 if weak else 0.9,
                "model_metadata.market_state": "RANGE" if weak else "TREND",
                "model_metadata.decision_audit.intrabar_confirmed": 0.0 if weak else 1.0,
            }
        })
    return rows


def test_numeric_association_ranks_signal():
    result=_numeric_associations(_rows(),"direction_weak",40)
    assert result
    assert result[0]["feature"] in {
        "entry_features.edge_ratio",
        "model_metadata.decision_audit.intrabar_confirmed",
    }
    assert abs(result[0]["standardized_mean_difference"]) > 1


def test_categorical_association_reports_rate_spread():
    result=_categorical_associations(_rows(),"direction_weak",40)
    assert result[0]["feature"] == "model_metadata.market_state"
    assert result[0]["rate_spread"] == 1.0


def test_summary_exposes_safeentry_candidates():
    s=summarize(_rows(),{"total_trades":60,"feature_joined":60},min_coverage=40,top=5)
    vals=s["safeentry_ablation_candidates"]["intrabar_confirmed"]
    assert vals
    zero=next(v for v in vals if v["value"] == "0.0")
    one=next(v for v in vals if v["value"] == "1.0")
    assert zero["direction_weak_rate"] == 1.0
    assert one["direction_weak_rate"] == 0.0
