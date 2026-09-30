# Experimental role predictions (EA v0.53.5)

The model service defaults to `RAMON_ROLE_MODE=shadow`; Compose pins this value.
Only Chronos' base decision, reason and edge reach execution. The role ensemble
is inactive and its executable risk multiplier is exactly 1.0. The separate
shadow risk probability is telemetry only. Existing EA entry/exit rules continue
to operate as before.

Training writes `checkpoints/ensemble/shadow/current.json` and immutable versions.
It never writes `active.json`, promotes a live bundle or changes live training
thresholds. Each available model is trained independently. The existing minimum
of 40 training examples, with at least 10 examples of each class, is retained.
Regime uses completed historical candle outcomes and can train without closed
trades. Entry, news and SL risk need clean closed trade outcomes. Meta additionally
needs disjoint purged base and later training windows. No prediction is invented
when data is insufficient. These outputs are experimental, without validated
accuracy or calibrated confidence claims.

## Install and train on your current database

```bash
cd ~/Documents/Presentation/ramon-ai-trader
git fetch origin feature/role-predictions-shadow-v0535
git switch feature/role-predictions-shadow-v0535
docker compose build model
docker compose --profile tools run --rm tools -m ramon.shadow_roles
docker compose up -d --no-deps model
curl -s http://127.0.0.1:8012/health | python3 -m json.tool

MT5_R="$HOME/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/Ramon"
cp mt5/Ramon.mq5 "$MT5_R/Ramon.mq5"
```

Compile the copied EA in MetaEditor and reload it on each chart. Keep your existing
EA input settings. Retrain with the same tools command when more closed outcomes
arrive, then restart `model` to load the new shadow version. Training does not
automatically restart or modify the running service.

## Read the panel

`SHADOW | TREND R:70% E:N/A N:N/A M:N/A SL:N/A`

- `R`: probability of a trend-like next eight M15 bars; this is not BUY/SELL.
  Below 50%, the displayed label is `RANGE/UNCLEAR`.
- `E`: estimated positive net outcome for the dominant Chronos candidate direction.
- `N`: news-role estimate of a positive net outcome for the candidate.
- `M`: combined positive-outcome estimate from the later Meta training window.
- `SL`: estimated full-stop-loss probability; display only.
- `N/A`: no valid model prediction is available.

`/health` exposes `shadow_roles` with each role's sample count and waiting reason,
plus bundle loading errors. `/decision` also includes per-role inference errors.
`ensemble_active=0`, `risk_model_ready=0` and `risk_multiplier=1.0` confirm isolation.
Untrained, corrupted, incompatible or wrong-symbol shadow models cannot veto or
replace a base decision. Live model artifacts are not loaded in shadow mode.
