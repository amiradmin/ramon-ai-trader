# Offline paper-label research (no live execution)

Run `python -m ramon.offline_paper_labels --help` for parameters. PostgreSQL `ramon.decision_samples` and `ramon.mtf_utc_bars` are read with a **READ ONLY** transaction. The script writes a research CSV file, not training labels into canonical trade outcomes, and never activates an EA or bundle.

For every historical immutable decision with a BUY/SELL direction, entry is at the **next complete M1 open after the quote**. It applies observed snapshot spread as a conservative round-trip proxy and evaluates a fixed price TP/SL with a bounded M1 horizon. Same-bar TP and SL are ambiguous and never counted as wins; missing/gapped bars cannot be labeled. WAIT is not a hypothetical executed trade. The model can only be trained on features that existed at quote time, not on calculated outcomes.

**Important**: M1 OHLC bars cannot reconstruct actual bid/ask fills, intra-minute ordering, latency, commission, dynamic spread or slippage. Values in `net_price` are simplified paper prices, **not actual net P&L or R-multiples**, and may differ substantially from executable results. Bars near market close and incomplete horizons are excluded. The report does not demonstrate that any strategy is profitable.

The available count of decisions is not the count of independent observations: de-duplicate repeated snapshots per signal bar, group by day, and use purged chronological evaluation before any model training or candidate promotion. Do not label opposite-direction execution as a correct trade based solely on a favorable future move. Do not use these paper labels in `train_roles.train_bundle`, whose promotion gate is based on independently observed actual closed-trade outcomes.

Research architecture: PostgreSQL is the authoritative input; CSV is a disposable intermediate output in `/tmp` only. Persisting versioned paper labels into a separate PostgreSQL research schema, plus a shadow-only Logistic Challenger and validation dashboard, are future steps. No Dashboard Opportunity development is in scope.
