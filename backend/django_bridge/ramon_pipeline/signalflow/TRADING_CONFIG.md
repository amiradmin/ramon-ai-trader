# Ramon trading configuration

The runtime reads `trading_config.json` beside the SignalFlow Python modules. The JSON file is the single editable source for opportunity thresholds and setup switches; its contract is documented by `trading_config.schema.json`.

## Safe editing

1. Edit `trading_config.json` and validate it as JSON.
2. Keep `validation.allow_lookahead=false` and `use_completed_candles_only=true`.
3. Run the SignalFlow and Django test suites before deploying.
4. Restart the Django/worker processes after changing the file so newly constructed detectors load the new values.
5. Record the config version/hash with every backtest. Do not compare results produced with different settings as if they were the same strategy.

## Opportunity setups

- `trend_pullback` and `breakout_retest` preserve the existing production research path.
- `failed_breakout_sweep`: a completed candle sweeps a prior range extreme and closes back inside.
- `range_edge_fade`: rejection at a prior range boundary, enabled only in a classified ranging regime.
- `session_opening_range_breakout`: close outside the completed London/New York opening range. Session timezone is IANA/DST-aware.
- `volatility_compression_expansion`: a directional expansion candle after a compressed prior range.
- `exhaustion_reversal`: extended move plus confirmed swing-level CHoCH; intentionally last and highest-noise.

The five supplemental setups are **disabled by default**. Enable one at a time only after a setup-specific chronological backtest. The shared completed-bar, regime-stability (where applicable), volatility, Entry Timing, Risk Management, and Django authorization gates remain in force. A detected opportunity is not an order instruction.

## Strictness

Tune numeric thresholds in each setup's JSON object. Lower buffers / rejection requirements generally make detection more permissive; higher requirements make it stricter. Change one parameter at a time and keep a copy of the prior config. There is intentionally no global preset that silently rewrites individual thresholds.

## Important boundaries

This file configures SignalFlow opportunity detection. Entry Timing and Risk Management still have their own Python dataclasses and defaults; the JSON currently documents their thresholds for centralized planning but does not yet override those stage constructors. The risk stage and server-side command authorization must never be loosened by an Opportunity Finder profile. The `minimum_risk_reward` entry is descriptive until the RiskConfig loader is wired in a separately tested change.
