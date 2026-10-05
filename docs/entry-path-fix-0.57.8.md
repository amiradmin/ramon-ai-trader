# Entry-path correction — Ramon 0.57.8

## Behavior

- The EA accepts `manual_override_range_pass` for BUY and SELL range candidates. Direction-specific automatic range reasons must still match the direction. Range capability, saved model response, boundary geometry, loss budget, midpoint target, quick-target reward/risk and cooldown checks remain required.
- News readiness/window, configured account-loss limits, same-direction loss cooldown, daily entry cap and spread cap apply to dashboard opportunity commands as well as automatic and analytical manual-pass entries.
- A human dashboard selection can still bypass analytical confirmations. Existing automatic single-position behavior and dashboard hedging/count constraints are unchanged.
- Account-loss limit inputs retain their existing values. If `EnableAccountLossLimits` is false, the account-loss gate remains disabled for all three routes; no percentages were invented or enabled by this update.

## Validation

73 related tests passed: shared entry gates, direction-compatible automatic and manual range protocol, actual account-loss and cooldown arithmetic, position guard ordering, decision HTTP round trip, saved range provenance, timing, early exits, TP stages, outbox attribution and opportunities.

The source-text position tests and C++ test adapters were brought up to date with existing production behavior. Production exit logic was not changed.

The separate legacy `tests/test_ea_source.py` suite still has 20 pre-existing failures, also reproduced against the original 0.57.7 source. Most stop at hard-coded 0.54.9 version/layout assertions. Those assertions are not a passing release gate for current versions and were not rewritten to hide failures.

MetaEditor compiled the staged source with **0 errors and 0 warnings**. Source and executable were installed using a fresh no-managed-position/no-pending-command check; installed hashes match the verified staged files. No test orders or live control changes were made.

## Artifacts and activation

- Source: `mt5/Ramon.mq5`.
- Verified source, executable and compiler log: `artifacts/ea-0.57.8/`.
- Prior installed files: `artifacts/ea-0.57.8/previous-0.57.7/`.

At the last verification, the MT5 diagnostic still reported **0.57.7** from the already loaded chart instance. Reloading the EA with the existing saved input settings is required to activate **0.57.8**; installation alone is not evidence of runtime activation. The assistant has no native MT5 UI control in this session. Activation confirmation is pending the user's reload.
