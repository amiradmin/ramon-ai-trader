# Offline tick-to-first-touch dataset — Ramon V2

This is **not** a live trading component. It accepts two independently recorded CSV inputs and emits label-only CSV output.

## Inputs

`opportunities.csv` (levels must have been committed at the original decision time):

```csv
opportunity_id,direction,entry_ms,entry_price,stop,target,horizon_ms
decision-a,BUY,1790000000000,4200.42,4198.42,4203.42,900000
```

`quotes.csv` (historical **executable** Bid/Ask observations, Unix milliseconds, globally sorted and unique):

```csv
time_ms,bid,ask
1790000000000,4200.00,4200.42
1790000001000,4200.02,4200.44
```

Prices above are **illustrative only**, not real market observations. If quotes do not cover the requested window adequately, the output is `UNKNOWN`, never a presumed loss or win. Buyer exits are checked against Bid; seller exits against Ask. The executable entry price and stop/target must be the originally recorded plan, not reconstructed from future bars. Quote arrival gaps, broker policies, slippage and limit fills can affect actual outcomes.

## Run

```bash
uv run python -m ramon.tick_path_dataset \
  --opportunities data/decision_entry_plans.csv \
  --quotes data/recorded_bid_ask_ticks.csv \
  --max-gap-ms 60000 \
  --output data/shadow_first_touch_labels.csv

uv run pytest -q tests/test_tick_path_dataset.py tests/test_tp_sl_shadow.py
```

Do not push account transaction history or sensitive real-time broker logs to the public repository.

## Explicit limitations

- Real Ramon SQLite `history_bars` stores OHLC M15 and spread snapshots, **not** an independently verified full Bid/Ask tick archive; it cannot determine true first-touch order within an M15 candle.
- The existing `trade_outcomes` records executed outcomes, not unbiased counterfactual outcomes for vetoed historical opportunities.
- This adapter deliberately requires external recorded tick quotes and decision-time SL/TP plans. No fake/synthetic ticks are generated.
- Quotes must use the same epoch-millisecond time base as the entry plan; broker-to-UTC normalization must be audited separately.
- This is a label builder, not a calibrated probability model or strategy validation. Do not promote it without purged chronological holdout, quote coverage analysis, fee-inclusive net-R, baseline comparisons, and review.
