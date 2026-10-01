# Chronos Calibration Lab

This lab evaluates whether Ramon's stored Chronos-2 forecasts can be improved
without fine-tuning the foundation model itself.

It is research-only. It does not affect live decisions, thresholds, sizing,
entry, exit, SL or TP behavior.

## Data discipline

- Uses one immutable snapshot per `signal_bar_time`.
- Chooses the first snapshot for each completed M15 bar to avoid overweighting
  Ramon's 30-second snapshot cadence.
- Uses the close of the Nth observed future M15 bar as the realized target.
  This respects weekends and market gaps.
- Purges development rows whose forecast target overlaps the next test fold.
- Fits every calibration model on development data only.

## Models

The report compares:

- `raw`: unmodified Chronos forecast move, normalized by entry ATR.
- `bias_corrected`: subtracts the mean development forecast error.
- `linear`: ridge calibration of realized move on raw Chronos move.
- `regime_aware`: ridge calibration using raw Chronos move plus stored
  regime features (`ret_1_atr`, `ret_4_atr`, `ret_12_atr`,
  `range_12_atr`, `body_efficiency_12`).

Metrics are OOS MAE in ATR units, RMSE, residual bias and direction accuracy.

## Run

```bash
docker compose --profile tools run --rm tools \
  -m ramon.chronos_calibration_lab \
  --db /data/ramon_history.sqlite3 \
  --symbol XAUUSD_l \
  --horizon 4 \
  --folds 5 \
  --l2 1.0
```

A calibrated model should not be promoted merely because one metric improves in
one fold. The minimum next step is consistent walk-forward improvement, followed
by shadow deployment and forward observation.
