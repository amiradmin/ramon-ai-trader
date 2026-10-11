# TP/SL first-touch labels and calibration (shadow only)

This module does **not** predict probabilities or affect MT5. It provides two offline building blocks:
1. `first_touch(PathRequest, list[Quote])`: label first *observed* executable quote-side touch in a bounded horizon.
2. `calibration_report(rows)`: evaluate supplied, point-in-time TP-first probability estimates with Brier score and decile reliability bins.

## Quote and event contract
- Quotes must be chronological observations with `time_ms` in the **same Unix-millisecond clock** as the entry. Require `bid < ask`, positive finite prices.
- BUY target/stop trigger on **Bid**; SELL on **Ask**. The entry price must be a previously observed/confirmed relevant side, or clearly marked as hypothetical in upstream data.
- Unknown or incomplete coverage remains `UNKNOWN`, not a fabricated TP/SL result. A complete observed path without a hit is `TIMEOUT`.
- `max_gap_ms` is an explicit upper bound; meaningful quote gaps before a touch make the event UNKNOWN. Strictly increasing timestamps are enforced.
- Quote first touch is a **path label**, not proof of order fill. Real execution depends on broker stop/limit rules, order delay, margin, commission, slippage and fills.
- Each requested path requires independent first-touch labels based on future *observed* quotes. Do not use M15 high/low to infer intrabar order.

## Offline calibration CSV
```csv
opportunity_id,label,p_tp_first
sample-1,TP,0.72
sample-2,SL,0.33
sample-3,TIMEOUT,
sample-4,UNKNOWN,
```

```bash
uv run pytest -q tests/test_tp_sl_shadow.py tests/test_trade_memory.py tests/test_no_trade_shadow.py tests/test_trade_audit.py
uv run python -m ramon.tp_sl_shadow data/holdout_scored_paths.csv --min-samples 30
```

**Crucial:** `p_tp_first` must be supplied by a *separately trained and chronologically validated* probability model using only features known at entry. The module does not derive the probability itself. Missing predictions on resolved paths are rejected. Censored paths are excluded from the conditional TP-vs-SL Brier score and the censor fraction is reported.

## Gates before considering deployment
- Tie prediction IDs, snapshot timestamps and quote archive provenance to immutable decisions; validate tick coverage and broker quote semantics.
- Use purged/embargoed chronological splits and held-out probability calibration; do not select thresholds on the same holdout.
- Report Brier versus a base-rate predictor, calibration reliability, coverage, TIMEOUT/UNKNOWN rate and realized net-R/expected value **after** spread and fees.
- Report precision/uncertainty, trade counts, regime and time segmentation.
- Preserve V2 broker-risk constraints, authorized no-order Cent lock, stopout-history recovery and account coordinator.
- Only separately reviewed, reversibly enabled live code may use this estimate; this PR changes none of that.
