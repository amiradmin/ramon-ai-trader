# Ramon research-only direction and timing shadows — 2026-10-08

Both labs are completely **offline**. They do not alter the model HTTP service, dashboard, EA, risk, or live decision. Research dependencies are not installed in the trading container.

## Retrieve ALL commits with one pull
Run from ~/Documents/Presentation/ramon-ai-trader:
```bash
git status --short
git pull --ff-only origin main
uv run --extra research --extra dev pytest -q tests/test_conservative_ai_policy.py tests/test_kronos_xgboost_shadow.py
```
If git refuses to fast-forward, do not reset or discard local changes; inspect status first.

## Offline XGBoost M1 Entry Timing
Requires real M1 history with enough contiguous bars in history_bars. It cannot learn M1 timing from the Kaggle M15-only database.
```bash
uv run --extra research --extra dev -m ramon.xgboost_timing_shadow --db data/ramon_history.sqlite3 --symbol XAUUSD_l --workers 2
```
This compares chronological purged train/validation/test AUC and Brier scores on hypothetical fixed TP/SL outcomes. Spread is included as a rough fill cost; same-bar TP/SL hits count as loss. It is not a tick-quality execution replay, not backtest profit, and uses a fixed demonstration geometry.

## Offline Kronos-small M15 Direction
Official model source/API: https://github.com/shiyu-coder/Kronos ; checkpoints: https://huggingface.co/NeoQuasar/Kronos-small and https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base .
Clone upstream Kronos into a separate research directory and install its published dependencies in an isolated environment. Check licenses and resource limits before downloading the model. Example:
```bash
git clone https://github.com/shiyu-coder/Kronos.git /tmp/ramon-kronos
# in an isolated environment with Kronos upstream dependencies installed:
python -m ramon.kronos_direction_shadow --db data/ramon_history.sqlite3 --kronos-source /tmp/ramon-kronos --max-samples 100 --stride 16
```
The evaluator uses completed M15 OHLC histories, rejects discontinuous windows, produces direction accuracy vs previous-bar direction baseline; no trading decisions or risk actions.

## Critical limitations and next promotion criteria
- Reports have not been run against the user's local SQLite data by GitHub edits alone. Running the scripts requires data/model download on the user's machine.
- 100 recent samples is a smoke evaluation, NOT proof of generalization. Expand to representative multi-regime forward holdouts, compare to Chronos and simple baselines, calculate trading costs, calibrate probabilities and validate entry opportunity sampling.
- Neither model may affect BUY/SELL/WAIT before prospective shadow performance and execution-aware out-of-sample metrics justify promotion.
- Existing conservative AI flag still defaults OFF. Do not turn it on live solely because five selected losing trades are blocked.
