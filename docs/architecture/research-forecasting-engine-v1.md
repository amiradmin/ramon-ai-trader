# Research Forecasting Engine v1

## Scope and isolation

This milestone adds standalone regression experiments alongside the existing ARIMA/SARIMA research utilities. It does **not** modify, import into, or connect to the Django decision, strategy, risk, command, or MT5 execution systems.

The new package is `ramon_pipeline.research_forecasting`. It accepts an ordered sequence of completed OHLCV candle mappings and returns diagnostic forecast reports. It has no database access and no trade/order API.

## Models

- **Ridge regression**: standardized lagged returns, rolling realized volatility, candle range/body/location, and optional tick-volume changes.
- **Random Forest regression**: nonlinear benchmark over the same features.
- **ARIMA/SARIMA**: existing research-only implementations remain unchanged in `ramon_pipeline.signalflow.forecasting`.

The target is the forward log return over a configurable horizon (default four candles). The latest forecast is a research estimate, not a BUY/SELL signal.

## Evaluation safeguards

- Input must be completed, ordered OHLCV candles supplied by the caller.
- Features at candle t use data available no later than t.
- Training targets are excluded unless their full forward horizon is observable.
- A chronological holdout is used; samples are never shuffled.
- A zero-return baseline is reported alongside MAE, RMSE, and directional accuracy.
- Missing volume is supported by omitting volume features; invalid OHLC and non-finite values are rejected.

This first version is a fixed holdout experiment, not walk-forward retraining or proof of profitability. It does not include spread/commission/slippage because the target is forecast error rather than a trading backtest. A forecast model should only be considered useful if it beats baselines consistently and a separate cost-aware backtest demonstrates incremental value.

## Dependencies and execution

The scikit-learn dependency is included in the optional `research` extra in `pyproject.toml`; it is not added to the Django runtime requirements. Import this module only in a research environment with the extra installed.

Example:

```python
from ramon_pipeline.research_forecasting import evaluate_regression_forecasts

reports = evaluate_regression_forecasts(completed_candles, horizon_bars=4)
for name, report in reports.items():
    print(name, report.as_dict())
```

## Explicit non-goals

- No Django URL, view, model, admin, settings, migration, or decision/risk changes.
- No change to ARIMA/SARIMA behavior.
- No TensorFlow/LSTM dependency in v1; deep learning is deferred until simpler models establish a credible out-of-sample baseline.
- No automatic model selection, ensemble voting, parameter changes, trade authorization, or live execution.
