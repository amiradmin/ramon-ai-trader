# Independent M1/M5 demo scalper

The scalper is a separate Django engine and worker. It shares the broker transport
and account risk budget with the main M15 engine, not its entry decisions or
position lifecycle. User-selected horizon: 1–5 minutes, automatic entries/exits
plus manual close controls. One scalp may coexist with a main position on a
hedging account. Netting accounts cannot provide independent same-symbol
positions and are rejected.

## Current state

Forward paper collection and the controls are deployed. Broker scalping defaults
OFF. Initial descriptive replay is negative after assumed costs; no trading edge
is established. New command EA v1.09 was compiled with 0 errors/0 warnings and
installed alongside v1.07 in the active MT5 Experts directory. It has not been
attached to the chart. Replacing the attached bridge and verifying real broker
lifecycles remain outstanding; compilation and tests do not prove broker fills.

Use `/api/ea/v1/scalper` for staff-session/CSRF-protected controls. Existing Django
staff credentials are required. No EA bearer token appears in the browser.
The main dashboard shows forward paper observations and links to these controls.
Start permits automatic demo entries only when server configuration and all
readiness checks pass. Pause stops new scalp entries; it continues managing time
exits. Close this scalp addresses one exact verified ticket. No close-all-by-symbol
operation exists. Failures and uncertain outcomes are visible and never blindly
retried. The deterministic close ID means a failed close needs reconciliation;
a second click does not create another broker attempt.

## Ownership and risk

Scalp commands use `engine=SCALPER`, magic 26092213, and an exact source/login/
server/symbol scope. A position must match both its magic and an executed,
authorized OPEN command's broker ticket. Main profit protection excludes scalp
commands. Scalp outcomes are tagged `SCALPER_M1_M5` and excluded from main learning
and its regressors. Account-level P&L naturally still includes both engines.

The maximum allowed planned scalp risk is 0.15% (new control rows still default to 0.1%) of balance, with volume rounded down and
no forced broker minimum. All account positions and pending/unobserved OPEN
reservations count against the existing demo total-risk cap (currently 2%). The
existing daily-loss baseline/limit also applies. A common heartbeat row lock
serializes creation with main entries. Main entry creation can coexist with only
verified scalp positions; unknown/manual positions preserve the previous
fail-closed behavior. Missing stop/contract/risk facts block new exposure.
Risk allowances cannot guarantee loss limits through gaps or exceptional fills.

The engine uses broker SL/TP plus a requested time exit five minutes after the
acknowledged fill. Manual/time closes require connectivity and can be delayed.
The default EA timer is 15 seconds, not tick-by-tick or subsecond execution. Django
retains every strategy/risk calculation; the EA checks scalp demo/hedging/magic
ownership and dispatches ticket-specific commands.

## Bridge and activation boundary

Run only one command EA bridge on the intended chart. To provide new telemetry,
replace the attached v1.07 with `RamonCommandEA_v1.09`, retaining the same instance,
URL and existing locally configured token. Do not attach a second EA alongside it.
The new bridge reports live `account_margin_mode`, magic and `ticket-magic-v1`.
The new EX5 is also available at `research/scalping/bridge/`.

Server gate `RAMON_SCALP_EXECUTION_ENABLED` defaults 0. It is separate from the
existing command gates, which are not changed. The configured scalp instance,
login and server fall back to the already configured demo profit-protection
scope; `RAMON_SCALP_INSTANCE`, `RAMON_SCALP_LOGIN`, `RAMON_SCALP_SERVER` can provide
an explicit separate scope. The worker creates a paused control row for that
matching feed. `RAMON_SCALP_SHADOW_ENABLED=1` enables paper collection only.
The control page shows why broker start is unavailable.

A deliberate activation requires the new bridge telemetry, verified hedging
mode, a configured demo scope and the separate server gate, followed by Start in
the authenticated console. This implementation leaves that gate and automatic
entries off. There is no retrospective order creation or replay-to-order path.

## Frozen hypothesis and execution assumptions

Version `m1-m5-pullback-v1` uses 64 consecutive completed M1 bars and 32 consecutive
completed M5 bars. M1 EMA9/21 and M5 EMA9/21 plus M5 slow-EMA slope must align.
A prior two-bar pullback to the fast EMA precedes a directional close through the
previous three-bar extreme. Stop: recent three-bar extreme plus 0.1 ATR buffer,
rounded to tick; allowed stop width 0.5–2 ATR; target 1.25 initial R. Total assumed
cost may consume at most 20% of risk. These are initial engineering hypotheses,
not fitted win probabilities or recommended investment parameters.

Paper decisions are recorded once per completed M1, with source/receipt cutoffs,
input checksum and frozen bars/quote/costs. Paper entry is the next full minute
AFTER recording, with no overlapping paper position. Outcomes use exact consecutive
M1 bid OHLC, ask-side short exits, stop-first ambiguity, adverse gap fills, assumed
slippage on both sides, and round-trip commission. First-observed outcome candles
are frozen incrementally. Missing/off-grid data is skipped after a ten-minute
receipt grace period; it is never filled or replaced by a later bar.
Live commands use the current quote within 15 seconds of a fresh evaluation;
this differs from the paper next-full-minute entry model and must be measured
separately. The live risk gate rechecks geometry, costs, sizing and shared risk.

## Verification

456 full PostgreSQL tests passed on an isolated internal test network. Another
22 focused tests passed, including both directions of parallel operation,
manual close ownership, cost arithmetic, stale data, outcome immutability,
CSRF/staff authentication, reserved risk and learning isolation. Migration parity,
JavaScript syntax, Compose configuration and shell syntax were checked. A fresh
MetaEditor log reports 0 errors, 0 warnings for v1.09; a nonempty EX5 was staged.
No broker test order or stop change was sent.

Official MT5 documentation requires checking trade-server return codes rather
than treating a successful method call as proof of execution:
[PositionOpen](https://www.mql5.com/en/docs/standardlibrary/tradeclasses/ctrade/ctradepositionopen).

Deployment verified at approximately 10:31 Tehran time November 10: Django healthy,
scalper-worker running, paper enabled, broker gate false, automatic control paused,
zero scalp broker commands. The first actual paper evaluation was WAIT /
M1_M5_TREND_NOT_ALIGNED. The attached EA still reports v1.07. Browser verification
confirmed the new public monitoring card and protected-console link. The v1.09
source/EX5 are installed beside the old version; artifact checksums and fresh
compiler log are saved under `research/scalping/bridge/`.


## Approved demo risk adjustment — November 10, 2026

User approved raising the active configured demo control to 0.150% and the engine
ceiling to 0.15%. Signal 61 SELL used bid 4112.66, stop 4114.93, point/tick 0.01,
tick value 1, and minimum/step 0.01 lot. With the existing 15-point cost allowance,
minimum reserved risk is $2.42. A $2,209.45 balance allowed $2.20945 at 0.1%,
but allows $3.314175 at 0.15%. Volume still rounds down; no forced minimum lot,
stop adjustment, retrospective order or account risk-cap increase is introduced.
This is a user-authorized sizing change, not evidence of improved profitability.
Regression tests cover the original rejection, approved minimum lot sizing and
rejection above the 0.15% ceiling. Actual broker fills remain separate evidence.
