# Django profit protection — 9 November 2026

## Decision and authorization

The user requested investigation of a missed TP, profit-protection code, activation
on demo after validation, and visible results in Ramon. After being informed that
breakeven underperformed the existing exits in the small historical sample, the
user explicitly chose to enable breakeven on demo. All calculations belong to
Django. No strategy or protection calculation was added to MT5.

The original 0.02 BUY filled at 4112.48, SL 4097.22973 and TP 4131.5428375.
The configured 1.25R target came from a 15.25027 structural risk distance, not a
resistance cap. Stop width was 2.328 setup ATR. Under the new rule its current-quote
trigger is 4127.73027 (before executable tick rounding). The new SL would be
4112.48; TP stays unchanged. Earlier historical peaks are not treated as a fresh
trigger when protection is enabled.

## Frozen-entry exit comparison

The reproducible local snapshot is `research/exits/snapshot.json`; run
`python3 research/exits/compare.py` and
`python3 -m unittest discover -s research/exits -p 'test_*.py'`.
`results.json` records its SHA256, source, symbol, coverage, actual persisted risk
thresholds, Git commit and dirty-source hashes. Git HEAD alone does not identify
the running policy because this workspace already contained uncommitted changes.
The dashboard receives the same report bundled under `ea_bridge/research_results`.

Source: ramon-command-ea-1, symbol XAUUSD_o, M15. Snapshot coverage is 26 October
2026 02:15 UTC through 9 November 2026 14:45 UTC. It contains 967 on-grid completed
M15 candles; 1,243 off-grid rows were excluded without editing stored history.
H1/M5 context was bounded by candle completion time. Historical received-at
availability is not reconstructed, so these are descriptive historical results,
not forward or untouched out-of-sample evidence.

The current full-policy replay selected 48 baseline trades. Exit comparisons use
38 identical entry plans with complete consecutive 24-bar windows; 10 incomplete
or gapped windows were excluded from every variant. Entry times remain frozen
when an exit finishes early. Consequently these totals are paired exit outcomes,
not independently re-entering one-position portfolio backtests.

| Exit rule | Trades | Net R | Average R |
| --- | ---: | ---: | ---: |
| Current target | 38 | 7.443 | 0.196 |
| Target capped at 1.2R | 38 | 6.408 | 0.169 |
| Target at 1R | 38 | 6.408 | 0.169 |
| Breakeven at +1R | 38 | 4.566 | 0.120 |
| Trail 0.5R after +1R | 38 | 4.190 | 0.110 |
| Half profit at +1R, remaining original target | 38 | 6.925 | 0.182 |

Assumptions: 50 symbol points spread, 5 points slippage on each side, zero assumed
round-trip commission, no swap, next-open entry, ask-side SELL exits, adverse stop
gaps, conservative stop-first handling. Protection uses actual slipped entry and
original fill-to-stop distance; P&L R is normalized by the original planned risk,
matching the baseline replay. Changes based on a candle extreme only become
active next candle. Partial profit uses ideal half-volume, without lot rounding,
and is research only. A 24-bar timeout is a research convention, not a live exit.
Costs are assumptions, not reconstructed broker quotes. Closed cumulative-R
drawdown is included in JSON/dashboard, not account percentage or intratrade risk.
No price forecasts are evaluated or claimed.

Wide stops above two causal 14-bar ATR: 7 trades, +0.5625R total, +0.0804R average.
Other stops: 31 trades, +6.8802R total, +0.2219R average. This small descriptive
split does not justify tightening live stops or imposing a new entry cap.
Targets, structural stop placement and entry policy were therefore retained.

## Implemented demo rule

Django calculates original R from the reported position entry and the original
executed OPEN command's stop. For BUY it uses current bid; for SELL current ask.
At +1R it requests entry-level SL, rounded toward protection to the broker tick.
It never deliberately loosens the reported current SL and preserves reported TP.
Price breakeven does not guarantee zero net loss after costs or gaps.

Protection requires an explicit configured instance/login/server scope, integer
broker demo mode, both existing command gates, the separate protection gate,
fresh quote and heartbeat, terminal permissions, valid contract metadata and an
exact account-scoped executed/authorized Ramon OPEN broker-ticket match. Changed
volume or target, unknown/manual positions and unsupported ticket mapping are
skipped. Quotes older than 30 seconds and heartbeats older than 60 seconds cannot
produce a new command. Broker minimum stop distance and tick size are checked.
Broker freeze levels remain subject to broker rejection; the old EA does not
supply this metadata.

One deterministic MODIFY_SLTP command is allowed per original OPEN. The producer
locks the heartbeat row, defers while another command is unsettled, and uses a
20-second command expiry. Failed, expired, uncertain or acknowledged commands
are not blindly retried. The rule runs at authenticated EA command polling, not
in a research GET endpoint. Acknowledgements use the existing command lifecycle.
Existing v1.07 supports MODIFY_SLTP, so replacing the attached EA is unnecessary.

This is server-polled protection, not tick-level protection: a peak between polls
can be missed. The broker ultimately validates the request against its current
position and quote. Avoid concurrent manual changes during a pending request;
legacy MT5 commands do not provide atomic compare-and-swap of position stops.
If the bridge loses connectivity, the last broker SL/TP remains in force.

## Visibility

The Ramon dashboard's Profit protection panel shows configuration mode,
calculation owner (Django), current R, current/proposed SL and preserved TP. The
broker-results table shows pending/claimed/executed/failed states and broker
response. Once a unique account/source/symbol/decision-linked closed outcome
arrives, its net P&L in account currency is shown. This does not estimate a causal
profit improvement from the stop change. The historical comparison is labelled
separately from actual broker results. No real-account execution is authorized.

## Verification and activation

The final full suite passed **422 tests** against an isolated PostgreSQL
17/TimescaleDB instance with no production or broker route. Five additional
paired-exit execution tests passed. Dashboard JavaScript syntax, Python syntax,
Compose configuration and patch whitespace checks passed. The disposable database
container was removed after testing.

Django was rebuilt and recreated with protection enabled only for the verified
instance/login/server tuple in local `.env`. Existing command gates were already
enabled and were not changed. The endpoint and rendered dashboard were verified
as `DEMO_ENABLED / MONITORING`, with the original BUY `WAITING_FOR_1R` and no
protection command yet. Activation did not manufacture a trigger from past highs
or send a test broker order. Actual results will populate after a fresh eligible
quote, broker acknowledgement and later closed-trade report.

## Numeric-ticket correction — 9 November 2026

The first real protection request triggered at 16:12:05 UTC (+1.0026R), but the EA
returned `FAILED / BROKER_MODIFY_0_unknown retcode 0 / broker_ticket 0`. Django
had encoded `position_ticket` as a quoted JSON string. The attached v1.07 EA's
`JsonNumber` parser passes that quoted value to `StringToDouble`, producing zero.
The initial tests exercised Django command/acknowledgement semantics but did not
check this actual EA numeric wire contract; their passing result did not prove
broker modification would succeed.

Django now serializes the ticket as an integer and rejects tickets outside the
exact positive IEEE-754 integer range supported by that parser. Regression tests
assert numeric type, ticket value, range rejection and bounded recovery. Existing
commands and broker responses remain immutable. Only the exact known failure
above, with a matching quoted ticket, permits one deterministic replacement
command using the corrected format, after all current quote/position/permission
checks pass again. Other failures and uncertain execution are not retried.
The dashboard now reports an existing command's state instead of implying it is
merely waiting for a trigger. All calculations and the correction remain in Django.
This rule moves the stop; it does not close a profitable position early.
