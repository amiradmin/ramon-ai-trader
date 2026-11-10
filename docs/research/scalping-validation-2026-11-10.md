# Initial scalping evaluation — 10 November 2026

Frozen policy: `m1-m5-pullback-v1`, defined before inspecting this replay's outcomes.
This is descriptive development evidence, not untouched out-of-sample validation.
No parameters were optimized against these results and no broker orders were sent.

Source: ramon-command-ea-1; LiteFinance-MT5-Demo; XAUUSD_o. Inputs: 3,188 M1 bars
and 1,242 M5 bars, November 5–10, 2026 (exact UTC times and hashes in the report).
No invalid/off-grid rows were excluded. There were 3 M1 gaps and 5 M5 gaps; rules
require consecutive warmup windows, and missing outcome intervals are not filled.
Historical receipt availability and executable quotes are not reconstructed.

| Assumption | Base | Stress |
| --- | ---: | ---: |
| Fixed spread, points | 22 | 44 |
| Slippage each side, points | 5 | 10 |
| Round-trip commission, points | 5 | 5 |
| Closed paper trades | 81 | 42 |
| Net R | -7.2244 | -0.7772 |
| Mean net R per trade | -0.0892 | -0.0185 |
| Win rate | 46.91% | 50.00% |
| Net points | -884 | -599 |
| Maximum closed cumulative-R drawdown | 7.9157 | 5.5759 |

Spread 22 points matched a contemporaneous quote, not reconstructed historical
spread. Commission 5 points is consistent with recent reported examples under
current point value and volume; it is an explicit fixed assumption. Slippage is
assumed, not estimated from a sufficiently measured sample. At point 0.01, base
all-in friction is 0.37 price units. Stress rejects additional opportunities through
the cost gate and consequently uses different trades; its score is not a paired
cost sensitivity or evidence that higher costs improve identical trades.

The no-trade baseline is zero points. Neither scenario beats it. This replay is
not a comparison against the current full M15 Ramon policy and does not establish
that scalping should replace or outperform it. No account percentage returns,
forecast accuracy or intratrade drawdown are claimed.

Artifacts: `research/scalping/snapshot-2026-11-10.json` contains exact replay bars;
`research/scalping/replay-2026-11-10.json` contains input/code checksums, Git commit,
persisted main thresholds, policy, costs, trades and rejection counts. Git commit
alone does not identify this dirty workspace; the scalping source hash is retained.

Reproduce with the saved bars and `replay_scalper`, or generate a new read-only
snapshot using `manage.py evaluate_scalping --source ramon-command-ea-1
--symbol XAUUSD_o --spread-points 22 --slippage-points 5 --commission-points 5`.
A newly queried dataset may have different coverage; retain its checksum.

Conclusion: keep broker activation off, collect separately labelled forward paper
observations, and measure actual execution costs before assessing a later frozen
candidate. This evidence does not support switching on this scalping policy now.
