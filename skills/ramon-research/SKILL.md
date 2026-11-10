---
name: ramon-research
description: Research and compare Ramon XAUUSD trading policies, forecasting models, and event context using causal data, explicit costs, and reproducible reports. Use for strategy changes and performance evaluation in the Ramon repository.
---

# Ramon research

Locate the Ramon repository from the active workspace. Read `docs/architecture/professional-demo-playbook.md` and the relevant report in `docs/research/` before changing a policy. This skill supports the user's requested experiment; it does not authorize external uploads, paid optimization, deployment, or broker orders by itself.

## Compare the actual policy

- The deployed strategy is `backend/django_bridge/ramon_pipeline/signalflow/`. `_build_pipeline(demo_mode=True)` in `ea_bridge/decision.py` includes persisted demo thresholds. Record those thresholds, commit, input checksum, source, symbol, timeframe, coverage, and costs with results.
- `manage.py evaluate_demo_playbook --help` describes the existing full-policy baseline versus ranked-plan replay. Both share current detectors; this is not an exact historical binary comparison. Report `validation` and all execution assumptions with the metrics.
- Keep source/symbol series separate. Exclude off-grid M15 candles explicitly; count exclusions. Do not silently repair stored timestamps, fill weekend gaps, or use forming candles. Historical H1/M5 inputs must have closed by the decision time. Received-at timestamps matter for forward claims.
- Replay entries occur on the next open. Include spread, slippage on both sides, and round-trip commission in symbol points. Preserve conservative stop-first handling, ask-side short exits, and the distinction between closed-equity and intratrade drawdown.
- Freeze a candidate before inspecting its later evaluation period. Warmup may precede that period; scored trades may not. A period inspected during development is descriptive, not untouched out-of-sample evidence. Compare unchanged-price forecasts and the existing strategy, not just raw accuracy or win rate.

## External research

Read LONA's strategy-authoring documentation before creating code and dry-run the complete source before saving. Confirm the symbol ID, broker/feed identity, interval and coverage. Use identical dates, exposure, costs and execution settings for each comparison. Research strategies under `research/lona/` are deliberately simplified challengers, not ports of the full SignalFlow engine. Do not compare their percentage returns directly against differently sized Ramon R results.

Use connected TipRanks economic-calendar tools for event snapshots. Follow `docs/operations/research-tools.md` to import them. Preserve the actual retrieval time. Naive provider timestamps have an unverified timezone: do not assume UTC, calculate event windows, or change orders from them. Snapshot imports are not a continuously connected runtime feed.

## Report and apply

Report trades, net expectancy, drawdown definition, costs, data gaps, period and sample size. Keep forecast error separate from trading P&L. A best historical score alone does not justify changing settings. Apply only the requested change, test meaningful causal and execution invariants, and leave an inspectable report describing remaining limitations. Demo status does not imply permission to change live-account settings.
