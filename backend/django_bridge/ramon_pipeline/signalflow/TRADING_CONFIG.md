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
- `volatility_compression_expansion`: the `compression_lookback_bars` candles immediately before the signal candle must fit in a total width of `compression_max_width_atr` ATR, and a directional candle (body >= `expansion_body_atr`) must close beyond that range. (The earlier `compression_max_atr` multiplied the limit by the lookback, which let ranges of about 15 ATR count as compressed; it was replaced.)
- `exhaustion_reversal`: extended move plus confirmed swing-level CHoCH; intentionally last and highest-noise.

The five supplemental setups remain **disabled in the standard profile**. Each has a separate `demo_enabled` switch. The decision endpoint applies those switches only after fresh MT5 telemetry positively identifies `ACCOUNT_TRADE_MODE_DEMO`; non-demo accounts continue to use the standard `enabled` values. In the current demo profile, all five supplemental detectors can be observed on the live market feed, with exhaustion reversal evaluated last.

Demo activation does not bypass the shared completed-bar, regime-stability (where applicable), volatility, Entry Timing, structural Risk Management, account-baseline, or Django command-authorization gates. A detected opportunity is not an order instruction. Actual order delivery still requires both server environment gates, a fresh matching demo heartbeat, a passing risk verdict, a valid daily baseline, and EA-side demo-account checks.

## Strictness

Tune numeric thresholds in each setup's JSON object. Lower buffers / rejection requirements generally make detection more permissive; higher requirements make it stricter. Change one parameter at a time and keep a copy of the prior config. There is intentionally no global preset that silently rewrites individual thresholds.

## Practical demo rollout

1. Deploy the code and run the full Django test suite.
2. Attach RamonCommandEA v1.04 to the intended XAUUSD M15 demo chart and verify the dashboard reports a fresh heartbeat, `DEMO` account mode, matching symbol, and terminal trade permission.
3. First observe decisions with server command creation/execution gates OFF. Confirm the Opportunity Finder reports each setup type and that Entry Timing/Risk Management explain every rejection.
4. Only when telemetry, daily baseline capture, stop/target values, and risk verdicts are verified should command creation and execution be enabled locally for the demo deployment. Never commit those environment switches as enabled.
5. Test one position at a time. The current command producer intentionally refuses to create a new OPEN command while any position is open because open-position risk is not yet independently verified.

The EA's local `EnableCommandExecution` input must also be enabled for the demo terminal. Its own account-mode check rejects non-demo accounts. Keep the EA source and its risk cap under version control; keep the bearer token and deployment switches local.

## Important boundaries

This file configures SignalFlow opportunity detection. Entry Timing and Risk Management still have their own Python dataclasses and defaults; the JSON currently documents their thresholds for centralized planning but does not yet override those stage constructors. The risk stage and server-side command authorization must never be loosened by an Opportunity Finder profile. Entry Timing and Risk Management now load their editable thresholds from the same JSON file as Opportunity Finder. Django must be restarted after edits. Risk limits remain independent safety gates and are never bypassed by a looser opportunity profile.

## Evaluation order and ranking

Core setups (`trend_pullback`, `breakout_retest`) are evaluated first with their existing rules. Supplemental setups are consulted only when the core path produced no approved signal, so they can never hide a core setup. Among supplemental setups, every enabled detector runs on the latest completed candle and the highest-scoring candidate is used; the others are listed in `setup.also_detected`. When a supplemental setup is used, `core_rejection` records why the core path declined.

## Optional higher-timeframe bias

`opportunity_finder.higher_timeframe_bias` is a veto for trend-following setups (`applies_to`). It groups completed M15 candles into complete hourly candles (no extra feed, no forming hour) and requires the hourly close to be on the setup's side of a rising/falling EMA (`ema_period`). A NEUTRAL or UNAVAILABLE bias vetoes the setup. Reversal and range setups are never filtered. It is off in both profiles (`enabled` and `demo_enabled` are false) until you choose to test it.

## Demo threshold profile

On a verified demo account the decision endpoint uses the expanded thresholds (`RAMON_DEMO_PROFILE=expanded`, the default). Set `RAMON_DEMO_PROFILE=standard` in the local `.env` to keep the supplemental setups on but run every threshold exactly as live, which is the right mode for validating the original pullback and breakout/retest rules. Each decision records `strategy_profile` (`STANDARD`, `DEMO_EXPANDED` or `DEMO_STANDARD_THRESHOLDS`) so results from different profiles are never mixed.
