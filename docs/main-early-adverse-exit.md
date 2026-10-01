# MAIN early adverse exit

MAIN now runs the existing early adverse exit after TP stage management. It
requests a close when floating loss reaches 60% of reconstructed initial SL
risk, the position is at least 120 seconds old, and two distinct valid model
responses consecutively provide no model, intrabar, or trend support for its
direction. SMALL retains its 50% threshold and risk cap.

Missing, invalid, pre-entry, or stale model responses cannot authorize this exit.
Failed requests clear the confirmation streak; successful responses publish a
separate snapshot timestamp. Recovery below the loss threshold or renewed
directional support also clears the streak. The broker SL remains in place;
60% is an evaluation threshold, not a guaranteed execution loss limit.

Successful closes retain the `early_adverse_exit` telemetry reason. The dashboard
reports MAIN's updated exit policy.

## Historical evidence

Read-only inspection of `ramon_audit_copy.sqlite3` found 107 closed trades:

- 31 broker SL exits, totaling -351.49 account units: 22 BUY and 9 SELL.
- Two recorded `early_adverse_exit` exits, totaling -13.23 account units.
  Their net losses were 0.713R and 0.410R and their recorded EA version was 0.36.
- Only 36 of 107 trades have positive recorded initial risk.
- The joined decision metadata does not identify entry reasons for the SL trades;
  three SL trades have no joined metadata at all.

These records demonstrate prior use of the exit, but do not establish its
counterfactual benefit or an optimal threshold. Account units are not assumed
to be USD. The 60% MAIN threshold is the existing configured value, not a value
fitted to this small historical sample. No causal entry-failure diagnosis or
profit improvement is claimed from these records.

## Validation and installation

The deployed snapshot is `mt5/Ramon_installed_early_adverse.mq5`, EA version
`1.540` (dashboard `v0.54.0`). It preserves the installed terminal's newer
research telemetry and profit-lock changes. The shared early-exit and risk-gate
fixes are also applied to `mt5/Ramon.mq5`; that baseline file retains its older
version and does not contain all installed telemetry extensions.

Version 1.540 enables `AllowMinLotRiskOverride` by default. MAIN may use only the
broker minimum volume when its planned SL risk exceeds the preferred $0.06
budget but stays within the existing $0.20 executable cap. Existing chart inputs
may retain their previous value. Risk diagnostics distinguish a disabled
override from an exceeded hard cap. Signal checks still control entry.

The installed source and executable were backed up before replacement. Runtime
reload is separate from installing files; confirm `v0.54.0` on the chart.

MetaEditor compiled the updated source with **0 errors and 0 warnings**. A
separate compiled baseline artifact is saved as `mt5/Ramon_early_adverse.ex5`.
The deployed snapshot was separately compiled with 0 errors and 0 warnings and
installed as `Ramon.mq5` / `Ramon.ex5` in the terminal.

Regression tests execute the actual MQL adverse-exit function through a C++
terminal API adapter, covering BUY/SELL, thresholds, age, repeated responses,
support recovery, stale/invalid data, new tickets, SMALL risk caps, closed
markets, and failed close retries. Source tests verify MAIN dispatch and response
timestamp publication. These do not replace MetaEditor compilation or testing
the installed Expert Advisor. Install the compiled artifact or recompile the
updated `mt5/Ramon.mq5` in the intended terminal before use.
