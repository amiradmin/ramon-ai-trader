from ramon.counterfactual_ablation import evaluate_policy, metrics


def _row(r, *, timing=1, forecast=.4, state="TREND_UP", moment="NORMAL", base="BUY", side="BUY"):
    return {
        "net_r":r,
        "direction":side,
        "labels":{"direction_weak":False,"adverse_first":False,"timing_stress":False,"exit_left_edge":False,"loss":r<0},
        "features":{
            "model_metadata.decision_audit.final.entry_timing_ready":float(timing),
            "entry_features.forecast_distance_atr":float(forecast),
            "model_metadata.market_assessment.state":state,
            "model_metadata.decision_audit.external_models.moment.moment_anomaly_label":moment,
            "core.base_decision":base,
            "model_metadata.decision_audit.final.market_direction":side,
        },
    }


def test_metrics_profit_factor():
    m=metrics([_row(1),_row(.5),_row(-.5)])
    assert m["net_r"]==1.0
    assert m["profit_factor"]==3.0


def test_timing_ready_blocks_unready():
    rows=[_row(1,timing=1),_row(-1,timing=0)]
    out=evaluate_policy(rows,"timing_ready")
    assert out["kept"]["trades"]==1
    assert out["kept"]["net_r"]==1


def test_v3_candidate_applies_independent_gates():
    rows=[
        _row(.5),
        _row(-1,timing=0),
        _row(-1,forecast=1.2),
        _row(-1,state="RANGE_MIDDLE"),
        _row(-1,moment="ELEVATED"),
        _row(-1,base="WAIT"),
    ]
    out=evaluate_policy(rows,"v3_candidate",forecast_limit=.8)
    assert out["kept"]["trades"]==1
    assert out["kept"]["net_r"]==.5
