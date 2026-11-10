# Professional trading practices in the Ramon demo playbook

Ramon's goal is a repeatable trading process: understand the market, compare
setups, wait for an executable entry, plan exits, and review the outcome.
There is no single professional trading strategy. The rules below are explicit
engineering hypotheses, not proof of a profitable edge in gold CFDs.

## Research and translation into code

| Practice | Research basis | Ramon implementation |
| --- | --- | --- |
| Separate a setup from its entry trigger | [CME: trading strategies in a trade plan](https://www.cmegroup.com/education/courses/building-a-trade-plan/trading-strategies-in-your-trade-plan) distinguishes conditions from triggers and calls for explicit exits before entry. | All seven enabled setups are previewed independently. Every candidate goes through quote-aware entry timing and sizing before selection. |
| Read support/resistance and market structure | [CME: support and resistance](https://www.cmegroup.com/education/courses/technical-analysis/support-and-resistance) describes prior highs/lows, moving averages, and broken levels changing roles. | Confirmed, unbroken M15/H1 swings cap trend targets; range fades target the midpoint. A nearby obstacle cannot be ignored to manufacture an attractive reward/risk ratio. |
| Put individual entries in wider context | [Barchart's practitioner webinar](https://www.barchart.com/education/webinars/131/using-multiple-time-frame-analysis-for-better-trading-entries) discusses longer-term direction and shorter-term entries. | H1 EMA direction and M5 close-through triggers contribute to ranking. These particular periods and weights are Ramon hypotheses. |
| Journal the reason, not only P&L | [CME: keep a trade log](https://www.cmegroup.com/education/courses/building-a-trade-plan/keep-a-trade-log) recommends recording entries, exits, levels, time, indicators, and reviewing strategy results. | Save every candidate's stage reasons, score components, context status, target source, and selected plan in the decision record. Existing linked outcomes retain that feature snapshot and setup tags. |
| Consider scheduled macroeconomic catalysts | [CME: economic events](https://www.cmegroup.com/education/courses/master-the-trade-futures/expanding-your-futures-knowledge/master-the-trade-economic-events) recommends a plan for releases. [Chicago Fed research](https://www.chicagofed.org/publications/chicago-fed-letter/2021/464) examines real rates, inflation expectations, and economic pessimism as gold drivers. | A verified economic-calendar/news feed is still absent. The robot does not claim to understand CPI, NFP, FOMC surprises, real yields, or the dollar. Event-driven entries require a separate timestamped data integration. |
| Test decisions chronologically and account for costs | [Bailey et al.: probability of backtest overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) explains why selecting an attractive backtest can produce misleading results. | A read-only replay compares fixed policies using next-open entries, explicit costs, and completed context. It does not tune parameters or certify performance. |

## The implemented decision process

The expanded demo profile enables the comparison path by default. Standard and
non-demo decision profiles retain their existing selection path. Setting
`RAMON_DEMO_COMPARE_CANDIDATES=0` restores single-setup selection; expanded-demo
entry quality still uses the quote-based calculation. No execution switch is
enabled by this setting.

1. Validate completed M15 candles. Reject shifted duplicates less than 900 seconds
   apart; they are not additional observations.
2. Preview trend pullback, breakout/retest, failed breakout sweep, range-edge fade,
   session opening-range breakout, compression/expansion, and exhaustion reversal
   independently, subject to their existing regime and volatility rules.
3. Compute entry quality from spread, distance from the signal, and freshness.
   The previous recent-movement proxy is retained as `legacy_entry_momentum`,
   rather than used to veto an otherwise timely expanded-demo entry.
4. Size each valid entry with the existing stop, broker metadata, and risk policy.
5. Plan the target before ranking: range midpoint for a range fade; otherwise the
   configured R target capped 0.10 ATR before the nearest unbroken confirmed swing.
   Swing confirmation requires three completed candles on each side. A close
   beyond a historical level removes it as an unbroken obstacle. Reject a target
   below the configured minimum reward/risk instead of widening it artificially.
6. Prefer fully executable candidates, then rank them using 40% setup score,
   25% computed entry quality, 15% H1 direction, 10% M5 confirmation, and 10%
   available target reward/risk (saturating at 3R). These are transparent initial
   weights, not independent probabilities or optimized estimates.
7. Commit duplicate-signal memory only for the selected executable candidate.
   Keep failed alternatives available for later evaluation.
8. Pass the exact selected stop/target/volume through the existing risk verdict
   and command producer. The decision endpoint still does not itself place orders.

For context, H1 uses EMA20/50 direction and slow-EMA slope with at least 60 bars.
M5 uses EMA8/21 direction with at least 24 bars; a close beyond the preceding
three highs/lows supplies a trigger. Missing, stale, invalid, or recently gapped
context is explicit and neutral in ranking. It is never invented confirmation.
Opposing context penalizes a plan rather than prohibiting every reversal trade.
Context is scoped to the same EA source and symbol, with candle completion and
receipt times bounded by decision time.

The continuity checker now distinguishes a narrowly defined Friday-night to
Sunday-night timestamp pattern from weekday outages. It preserves the observed
bars without interpolation and marks the break as observed, with the broker
calendar explicitly unverified. See [H1 continuity and the forward candidate
journal](../operations/candidate-journal.md) for the exact rule and measurement
limits. The journal records new demo candidates before their later price windows;
it does not fabricate entries for non-directional rejections or train live weights.

The session detector now needs the complete M15 opening range and a fresh close
across its boundary. Continued closes beyond the level are not new breakout
triggers. Compression uses the immediately preceding range with square-root-of-
time normalization; expansion must close outside it. Both changes apply to the
shared detectors wherever those setups are enabled.

The dashboard's **Opportunity comparison** table exposes each candidate's rank,
entry result, sizing/target result, and H1/M5 availability. These are saved decision
diagnostics; they do not prove that an order was delivered or filled.

## Read-only evaluation

After deploying this source, run a comparison against the matching demo EA's
persisted candles. Supply execution-cost assumptions explicitly, for example:

```bash
docker compose exec django python manage.py evaluate_demo_playbook \
  --source ramon-command-ea-1 --symbol XAUUSD_o --limit 2000 \
  --spread-points 50 --slippage-points 5 --commission-points 0
```

Those costs are illustrative. Zero commission means zero assumed commission,
not a claim about the broker. `--balance` defaults to a standardized paper balance
of 10000; supply the desired test balance explicitly. Current demo symbol metadata
is reported with the assumptions and held constant throughout the replay.

The report includes both policies' trades, net R, average net R per trade, win
rate, closing-balance drawdown, skipped-entry reasons, context availability, and
the count of off-grid M15 records excluded. Off-grid records are excluded from
the evaluation only; the command never deletes history or writes trading commands.
The baseline uses single-setup selection with the earlier momentum-based entry
quality; both policies use the current shared pattern definitions and thresholds.

The replay sees only completed signal/context candles before each entry. A new
position enters at the following candle's opening quote. SELL exits use ask
prices. Stop gaps receive the adverse opening fill, and an ambiguous intrabar
stop/target sequence resolves stop-first. One position is held at a time.

Limitations: fixed costs and current contract metadata, no tick-path reconstruction,
no historical swap/funding charges, incomplete historical context coverage, and
drawdown measured on closed equity only. The default 24-bar time exit is an
evaluation convention, **not** a new live close-position policy. Historical
backfilled context is evaluated by completion time; real online context additionally
must have been received before the decision. Changing historical candles after
the fact is not modeled.

The replay is descriptive. Use a subsequent untouched period and continued demo
trades to judge whether the policy helps. Do not repeatedly change weights against
the same sample and call it out-of-sample validation.

## What this version does not yet implement

The robot exits through its broker stop and target. An explicitly enabled, account-scoped
demo rule now allows Django to move SL to entry at +1 original R while preserving TP;
see [profit protection](../research/profit-protection-2026-11-09.md). All calculations
run in Django; MT5 only executes its command and reports the broker result.
It does not automatically scale out, trail stops, exit on regime changes, or pyramid positions. Such policies
need separate comparative evidence and broker-lifecycle tests. Existing learning
models remain research-only; they do not rewrite live ranking weights. Tick volume
is not treated as centralized gold order flow, and no undocumented “institutional”
signal is fabricated.

The repository has no configured Ruff, ty, or basedpyright gates. This feature uses
the existing Django/PostgreSQL test surface; adopting those tools remains a separate
tooling task rather than an implicit repository-wide migration.
