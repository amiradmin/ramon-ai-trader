import sqlite3

from ramon.core import Bar, Forecast, Market, Settings, evaluate
from ramon.history import ensure_history_db
from ramon.server import same_direction_sl_cooldown


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


def test_two_same_direction_sl_losses_start_two_bar_cooldown(tmp_path) -> None:
    db = ensure_history_db(tmp_path / "history.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.execute(
            """INSERT INTO trade_outcomes
               (trade_key,sample_key,symbol,direction,opened,closed,net_units,
                initial_risk_units,net_r,exit_reason,received,training_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("a","sa","XAUUSD_l","BUY",1000,1100,-10.0,10.0,-1.0,"DEAL_REASON_SL",1101,"LEARNABLE"),
        )
        conn.execute(
            """INSERT INTO trade_outcomes
               (trade_key,sample_key,symbol,direction,opened,closed,net_units,
                initial_risk_units,net_r,exit_reason,received,training_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("b","sb","XAUUSD_l","BUY",1200,1300,-10.0,10.0,-1.0,"DEAL_REASON_SL",1301,"LEARNABLE"),
        )

    assert same_direction_sl_cooldown(str(db), symbol="XAUUSD_l", direction="BUY", now=1301)
    assert not same_direction_sl_cooldown(str(db), symbol="XAUUSD_l", direction="SELL", now=1301)
    assert not same_direction_sl_cooldown(str(db), symbol="XAUUSD_l", direction="BUY", now=3100)
