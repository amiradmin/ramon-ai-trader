from ramon.shadow_missed_replay import replay_one, select_candidates

def candidate(**kwargs):
    row={"captured":100,"sample_key":"abc","signal_bar_time":0,"final_decision":"WAIT",
         "spread":0.2,"model_metadata":{"decision_audit":{
         "base":{"stop_distance":1.0,"target_distance":2.0},
         "final":{"candidate_direction":"BUY","reason":"wait"}}}}
    row.update(kwargs)
    return row

def test_deduplicates_intrabar():
    rows=[candidate(sample_key="a"),candidate(sample_key="b",captured=120)]
    assert len(select_candidates(rows))==1

def test_ambiguous_same_bar_not_profit():
    c=select_candidates([candidate()])[0]
    outcome=replay_one(c,[{"time":160,"open":100.0,"high":103.0,"low":98.0}])
    assert outcome["status"]=="AMBIGUOUS_SAME_BAR"

def test_tp_and_sl_separated():
    c=select_candidates([candidate()])[0]
    tp=replay_one(c,[{"time":160,"open":100.0,"high":103.0,"low":100.0}])
    sl=replay_one(c,[{"time":160,"open":100.0,"high":100.0,"low":98.0}])
    assert tp["status"]=="SIMULATED_TP"
    assert sl["status"]=="SIMULATED_SL"

def test_missing_m1_not_counted_as_winner():
    c=select_candidates([candidate()])[0]
    assert replay_one(c,[])["status"]=="INSUFFICIENT_M1"
