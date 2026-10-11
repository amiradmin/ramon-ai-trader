# No-Trade shadow: offline abstention benchmark

This is a **diagnostic**, not a live AI veto. It compares a historical baseline with a hypothetical candidate that declines some of those **same** opportunities.

Prepare an independently generated and time-consistent CSV:

```csv
opportunity_id,net_r,candidate_veto
example-1,-0.2,1
example-2,0.4,0
```

Each row requires an independently measured **net R** counterfactual for the original execution policy, even if the candidate would have vetoed it. Include spreads, commissions and slippage. The `candidate_veto` flag must have been computed using data available **at the original decision timestamp**, not by looking at the eventual outcome. Use unique opportunity IDs; do not treat executed trades as an unbiased record of rejected signals.

```bash
uv run python -m ramon.no_trade_shadow data/no_trade_replay_holdout.csv
uv run pytest -q tests/test_no_trade_shadow.py
```

The report exposes vetoed winners, vetoed losers, net-R difference and abstention rate, including how often the filter gives up profitable opportunities. Outputs cannot enable or modify any live policy.

## Required before operational use
- Produce candidate-veto values from timestamped market signals on a **purged chronological holdout**.
- Obtain independent tick/Bid/Ask counterfactual labels for both accepted and vetoed opportunities.
- Quantify incomplete coverage and uncertainty; compare to a no-veto policy on identical opportunities.
- Require an explicit, separately reviewed live activation change and a safe rollback.

This module does **not** estimate TP/SL probabilities. That requires labeled price-path data and separate calibration.
