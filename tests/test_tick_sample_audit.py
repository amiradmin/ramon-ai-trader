from ramon.tick_sample_audit import audit_rows
import pytest


def test_tick_validation_detects_bad_quotes_and_order():
    rows=[{'timestamp':'2021-05-24T00:00:00.100','bid_price':100,'ask_price':100.4},
          {'timestamp':'2021-05-24T00:00:00.050','bid_price':100,'ask_price':100.5},
          {'timestamp':'2021-05-24T00:00:00.050','bid_price':100,'ask_price':100.5},
          {'timestamp':'2021-05-24T00:00:00.150','bid_price':100,'ask_price':99},
          {'timestamp':'bad','bid_price':100,'ask_price':101},
          {'timestamp':'2021-05-24T00:00:00.150','bid_price':float('nan'),'ask_price':101}]
    r=audit_rows(rows)
    assert r['valid_rows']==3 and r['invalid_rows']==3
    assert r['backward_timestamps']==1 and r['duplicate_timestamps']==1
    assert r['median_spread_reference_points']==pytest.approx(50)
    assert r['can_calibrate_execution'] is False
