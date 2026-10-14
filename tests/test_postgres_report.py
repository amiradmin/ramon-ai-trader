from ramon.postgres_report import summarize_trades, generate_postgres_report
from unittest.mock import patch


def test_summary_financials_and_breakdown():
    rows = [
        {"trade_key":"a","net_units":2.0,"net_r":1.0,"direction":"BUY","exit_reason":"TP","closed":100},
        {"trade_key":"b","net_units":-1.0,"net_r":-0.5,"direction":"SELL","exit_reason":"SL","closed":200},
        {"trade_key":"c","net_units":0.0,"net_r":0.0,"direction":"BUY","exit_reason":"BE","closed":300},
    ]
    s=summarize_trades(rows)
    assert (s["trades"],s["wins"],s["losses"],s["breakeven"]) == (3,1,1,1)
    assert s["net_units"] == 1.0
    assert s["net_r"] == .5
    assert s["profit_factor"] == 2.0
    assert s["by_direction"] == {"BUY":2,"SELL":1}


def test_report_uses_postgres_reader(capsys):
    rows=[{"trade_key":"abc","net_units":1.2,"net_r":0.4,"direction":"BUY",
           "exit_reason":"TP","closed":100}]
    with patch("ramon.postgres_report.load_postgres_trades",return_value=rows) as reader:
        generate_postgres_report("private","XAUUSD_l",20)
    reader.assert_called_once_with("private","XAUUSD_l")
    assert "1.2000" in capsys.readouterr().out
