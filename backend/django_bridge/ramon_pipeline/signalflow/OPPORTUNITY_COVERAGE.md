# SignalFlow opportunity coverage

## Opt-in additions

- Trend-pullback experiments: retry a prior confirmed impulse, accept shallow pullbacks after a fast-EMA touch and resumption, and use the slow EMA as the value-zone reference.
- Independent setup families: range breakout and range mean-reversion.
- Reversal is only an interface stub and always returns `DEFERRED / NOT_IMPLEMENTED`.
- `MultiSetupDetector` runs enabled detectors, returns every candidate, selects the highest-scoring accepted candidate, and defers opposing accepted sides with `SETUP_CONFLICT`.
- Setup-type deduplication is scoped by symbol, timeframe, side, and setup anchor.

## Defaults and safety

All new pullback options and additional detector families are disabled by default. The existing `OpportunityDetector` remains the default stage in `SignalFlowPipeline`; using `MultiSetupDetector` is an explicit opt-in. Existing symbol-prefix gates remain in place.

The Django decision feature record logs `setup_type`, `pullback_variant`, and `impulse_rank` as flat fields. New detectors only produce candidates; downstream entry-timing and risk stages remain responsible for further approval. No EA-side strategy logic or order execution was added.

## Evaluation before enabling

Compare chronological XAUUSD M15 holdouts, separately by setup type:

1. Candidate count and unique signals after deduplication.
2. Signals approved by entry timing and risk management.
3. Win rate, expectancy, average reward/risk, and maximum drawdown.
4. Performance by market regime and volatility bucket.

No increase in trade count or profitability is assumed. The new detectors must remain disabled until causality, mirror-symmetry, regression, and chronological holdout tests pass.
