from unittest.mock import patch

from ramon.training_dashboard import _stats


def test_training_dashboard_uses_postgres_without_sqlite(tmp_path):
    sample = object()
    with patch.dict("os.environ", {
        "RAMON_TRAINING_BACKEND": "postgres",
        "RAMON_PG_TRAINING_DSN": "postgres://unused",
    }):
        with patch("ramon.postgres_training.read_training_examples", return_value=[sample]) as examples, \
             patch("ramon.postgres_training.dashboard_training_stats",
                   return_value=({"dashboard_trainable": 2, "dashboard_wins": 1,
                                  "dashboard_losses": 1, "dashboard_net_units": 0.2},
                                 [{"direction": "BUY"}])) as loader, \
             patch("ramon.training_dashboard.load_trade_examples",
                   side_effect=AssertionError("SQLite examples must not be loaded")), \
             patch("ramon.training_dashboard.sqlite3.connect",
                   side_effect=AssertionError("SQLite should not be opened")):
            stats, trades = _stats(tmp_path / "not-created.sqlite", "XAUUSD_l", "chronos")

    assert stats["trainable_samples"] == 1
    assert stats["dashboard_trainable"] == 2
    assert trades[0]["direction"] == "BUY"
    examples.assert_called_once_with("postgres://unused", "XAUUSD_l", "chronos")
    loader.assert_called_once_with("postgres://unused", "XAUUSD_l", "chronos")
