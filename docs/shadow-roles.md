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
git fetch origin main
git switch main
git pull --ff-only origin main
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

## Inspect losses and sizing

After rebuilding, run `bash scripts/analyze_ramon.sh --all`. The report now adds
`MAIN / SMALL ENTRY RISK AUDIT` and `SAME-DIRECTION REENTRY AFTER SL (UTC EVIDENCE)`.
Roles come from recorded entry role/magic; unrecorded or conflicting roles stay
UNKNOWN. Risk is compared with each trade's stored preferred budget and cap,
including the account-unit conversion recorded at entry. Do not compare old
SMALL trades with today's 2-cent cap.

Reentries are grouped by account and role, using recorded UTC offsets. The
current terminal cooldown requires two consecutive same-direction net-loss SL
positions, then waits 30 minutes from the most recent close. An entry after only
one SL is allowed. Review candidates in the uploaded closed-trade ledger are not
proof of an EA bug: absent outcomes or terminal history may change the streak,
and historical EA versions can have different rules. No trades are blocked by
this report and no avoided-loss or hypothetical-profit claims are calculated.

Training must run against the user's real database; repository changes alone
cannot train or load the models running on another machine. Health shows which
roles trained and which still need more data. Restart `model` after training.
