# Demo playbook validation — 9 November 2026

This records an initial descriptive comparison, not a profitability claim or an
untouched out-of-sample test. See the [policy and research basis](../architecture/professional-demo-playbook.md).

## Historical comparison

The exported demo snapshot contained 2,000 M15 rows from 27 October to 9 November
2026. Of those, 1,152 were off the UTC quarter-hour grid, including overlapping
timestamps seconds apart. The replay excluded those records without modifying the
database, leaving 848 correctly timestamped M15 candles.

Both policies used the same saved thresholds, the same starting demo snapshot,
and the same current contract metadata. The snapshot's per-trade risk ceiling
was 1.6%, with a 2% portfolio ceiling. No risk settings were increased for this
comparison. These are the existing saved settings, not new recommendations.

Costs were assumed at 50 spread points, 5 slippage points on each side, and zero
commission. The zero commission assumption must be replaced if the account pays
commission. Positions were resolved from OHLC bars, one at a time, with the
replay's 24-bar time-exit convention and conservative ambiguous-bar handling.

| Metric | Single-setup selection + supplied momentum quality | Ranked executable plans + calculated entry quality |
| --- | ---: | ---: |
| Simulated trades | 42 | 42 |
| Sum of net R | -3.765856 | 6.637586 |
| Mean net R per trade | -0.089663 | 0.158038 |
| Winning trades | 40.48% | 52.38% |
| Maximum drawdown on closed equity | 12.32% | 8.44% |

An R unit is each trade's planned stop risk; summed R is not portfolio percentage
return. Both policies use the current shared pattern detectors, so the table
compares selection, entry-quality, context, and target planning, rather than an
exact historical binary of the old deployment.

The ranked policy evaluated 489 flat-account entry snapshots. H1 context was
available for 387 and M5 context for 169. Missing, stale, insufficient, or recently
gapped context remained neutral. In particular, most of this short history cannot
measure the full effect of M5 confirmation. Session closures can also explain
some of the context gap flags.

This is encouraging evidence for continuing demo evaluation. It is only 42 trades
per policy across a short period. It does not establish durable expectancy, actual
broker fill quality, robustness to different costs, intratrade drawdown, or future
performance. No weights were fitted by the replay. The next evidence should come
from a later period using a frozen policy and realistic account-specific costs.

## Verification

Behavioral coverage includes independent setup previews, fallback when the first
candidate is unexecutable, quote-based entry quality, context completion and source
isolation, future-data exclusion, structural target checks, exact target persistence,
session breakout retriggers, compression geometry, gap fills, short ask-side exits,
and a replay command that does not create or alter trading records.

The full suite is run against a disposable PostgreSQL 17/Timescale container using
the existing Ramon runtime image. It has no route to the broker or production
database. Python syntax, dashboard JavaScript syntax, migration consistency, and
patch whitespace are checked separately.

Result: **376 tests passed**. Migration verification reported no changes; Python
and dashboard JavaScript syntax checks and patch whitespace checks passed.

The running demo service is not rebuilt or restarted by these checks. This source
change needs the normal Django deployment before it affects new demo decisions.
