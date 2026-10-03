# XGBoost research result — 2026-10-03

Local dual XGBoost regressors were fitted for net BUY/SELL R. This is an action-value model inspired by the publisher's tree approach, not a test of the original Hugging Face direction-classifier weights. The complete nine-candidate-per-fold inner results and exact implementation hashes are in `xgboost-evaluation.json`.

## Result

No candidate passed inner validation in any of the five folds. Therefore no outer refit was selected and the final outer policy used WAIT throughout, with zero trades. PF and MeanR are undefined; zero net R/drawdown indicate inactivity, not profitability. The broker-validation gate failed and no broker evaluation, Shadow integration, EA change or live service change was performed.

Unlike the first linear model, deeper tree candidates did produce substantial numbers of trades in **inner** validation. Every candidate with at least 30 trades had PF below 1. The largest PF among those candidates was 0.749894 (129 trades, MeanR -0.100656, fold 2, depth 4 / 150 rounds / threshold 0R). This failed evidence was used to reject candidates before outer scoring, not to pick a winning held-out model.

## Inner evidence

This table illustrates the least-negative MeanR among adequately sampled inner candidates in each fold; these are rejected candidates, not selected outer policies.

| Fold | Depth / rounds | Threshold R | Inner trades | PF | MeanR | Closed DD (R) |
|---|---|---:|---:|---:|---:|---:|
| 1 | 6 / 200 | 0.1 | 75 | 0.6935 | -0.1438 | 14.4084 |
| 2 | 4 / 150 | 0.0 | 129 | 0.7499 | -0.1007 | 19.7888 |
| 3 | 6 / 200 | 0.0 | 209 | 0.4978 | -0.2359 | 49.2934 |
| 4 | 6 / 200 | 0.0 | 136 | 0.3349 | -0.3898 | 54.9094 |
| 5 | 6 / 200 | 0.1 | 37 | 0.5895 | -0.1701 | 9.4126 |

All same-condition reference-policy metrics and the dataset fingerprint were verified identical to the previous cost-aware ridge experiment. That ridge policy had only six outer trades in one fold and did not pass either; it remains insufficient evidence of edge.

## Tick sample inspection

A separate bounded audit retrieved **300 tick records**, 100 per viewer window. The observed repository revision remained unchanged during fetching. No invalid Bid/Ask, backward timestamps or duplicate timestamps were detected within these windows.

| Viewer offset | First UTC (source-declared) | Last UTC | Median spread at reference point 0.01 |
|---|---|---|---:|
| 0 | 2021-05-24T00:00:00.256000+00:00 | 2021-05-24T00:00:54.768000+00:00 | 40.7 points |
| 1000000 | 2021-05-28T15:17:34.928000+00:00 | 2021-05-28T15:17:57.367000+00:00 | 31.4 points |
| 100000000 | 2023-05-24T19:29:57.679000+00:00 | 2023-05-24T19:30:38.308000+00:00 | 33.7 points |

These windows span seconds, not complete trading sessions. They do not support calibrating the spread distribution, complete-trade TP/SL order, slippage, or execution quality. The dataset card describes a reconstructed broker feed and declares UTC; that broker and clock were not independently verified. Viewer responses are not cryptographically pinned to the observed repo revision. Responses are saved separately with hashes; no source histories were merged and the 42-point assumption remains unchanged.

Attribution: Silva, C. (2026), XAU/USD Tick Data, [Hugging Face](https://huggingface.co/datasets/CarlosSilva1/xauusd-ticks), CC-BY-4.0.

## Validation and disposition

43 focused tests passed in the isolated research environment. Native JSON roundtrip, nonlinear learning, two-worker parameters, nested label purging, first-fold invariance to changed future prices, simulator label parity, input preservation and tick validation are covered. The completed source hashes and identical baseline metrics were also verified.

Keep this failed protocol reproducible. Do not interpret it as rejection of all XGBoost configurations or the remote pretrained model. Further candidate families require a new declared protocol and independent broker confirmation; do not adjust live filters from these results. Exact pull/install/test/run commands are in `xgboost-research.md`. EA and dashboard updates are unnecessary at this research stage.
