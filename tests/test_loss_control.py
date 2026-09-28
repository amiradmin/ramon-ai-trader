
from ramon.core import Bar, Forecast, Market, Settings, evaluate


class FixedModel:
    def __init__(self, forecast: Forecast) -> None:
        self.value = forecast

    def forecast(self, closes, horizon):
        return self.value


def test_extreme_completed_bar_trend_conflict_vetoes_buy() -> None:
    bars = []
    price = 140.0
    for index in range(128):
        close = price - 0.30
        bars.append(Bar(1_800_000_000 + index * 900, price, price + 0.05, close - 0.05, close))
        price = close
    last = bars[-1].close
    market = Market("XAUUSD_l", "M15", last, last + 0.04, 0.01, tuple(bars))
    model = FixedModel(Forecast(last - 1.0, last + 1.5, last + 2.0))

    result = evaluate(market, model, Settings())

    assert result.decision == "WAIT"
    assert result.reason == "trend_conflict"



from dataclasses import replace
import pytest
from ramon.ensemble import EnsembleCoordinator
from test_ensemble import _market, _decision


class ConfidentRole:
    def predict_proba(self, features):
        return 0.99


@pytest.mark.parametrize('buy', [True, False])
@pytest.mark.parametrize('active', [True, False])
def test_trend_veto_survives_meta(tmp_path, buy, active):
    coordinator = EnsembleCoordinator(tmp_path)
    coordinator.symbol = 'XAUUSD_l'
    if active:
        coordinator.regime = coordinator.entry = coordinator.meta = ConfidentRole()
    decision = replace(
        _decision(), decision='WAIT', reason='trend_conflict',
        buy_edge=2.0 if buy else -2.0, sell_edge=-2.0 if buy else 2.0,
    )
    payload, _ = coordinator.assess(_market(), decision)
    assert payload['decision'] == 'WAIT'
    assert payload['reason'] == 'trend_conflict'
    assert payload['edge'] == 0.0
    if active:
        assert payload['meta_probability'] == 0.99


def test_meta_can_still_accept_non_vetoed_signal(tmp_path):
    coordinator = EnsembleCoordinator(tmp_path)
    coordinator.symbol = 'XAUUSD_l'
    coordinator.regime = coordinator.entry = coordinator.meta = ConfidentRole()
    decision = replace(_decision(), buy_edge=2.0, sell_edge=-2.0)
    payload, _ = coordinator.assess(_market(), decision)
    assert payload['decision'] == 'BUY'
    assert payload['reason'] == 'ensemble_meta_up'


def test_server_does_not_gate_entries_on_uploaded_outcomes():
    from pathlib import Path
    source = (Path(__file__).parents[1] / 'src/ramon/server.py').read_text()
    assert 'same_direction_sl_cooldown' not in source
    assert 'loss_streak_cooldown_source' in source
