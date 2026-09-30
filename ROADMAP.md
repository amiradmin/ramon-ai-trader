# Ramon AI Trader Roadmap

This roadmap is the working plan for improving Ramon from a directional forecaster into a measurable, adaptive decision system.

## Guiding principles

- Improve only what can be measured.
- Keep execution changes behind shadow-mode validation first.
- Separate direction quality from entry timing and exit quality.
- Prefer real signal/outcome evidence over manual threshold tuning.
- Promote a new model or threshold only after it beats the current baseline on held-out data.

## Phase 1 — Signal Outcome Learning

Status: **IN PROGRESS**

Goal: measure what happened after every saved decision, including decisions that never became trades.

Work:
- Record outcome horizons at 1m, 3m, 5m, 15m and 30m.
- Measure direction accuracy.
- Measure forecast accuracy.
- Measure AI Trend accuracy.
- Measure Intrabar accuracy.
- Track False Entry and False Block cases separately.
- Attribute false blocks to Strength, Edge, Intrabar and AI Trend.
- Compare filters by regime and session as sample size grows.

Current implementation:
- `src/ramon/signal_outcomes.py`
- M1 outcome telemetry stored from completed micro bars.
- Read-only/shadow analysis only; no execution gate changes.

Exit criteria:
- Enough samples to estimate per-component accuracy with useful confidence.
- False-block reasons ranked by observed impact.
- Baseline report saved for later model comparisons.

## Phase 2 — Regime AI

Status: **NEXT**

Goal: classify the market before choosing an entry policy.

Initial regimes:
- Trending Up
- Trending Down
- Sideways
- High Volatility
- Low Volatility

Work:
- Train a regime classifier from historical bars and live decision samples.
- Keep regime prediction independent from the final BUY/SELL decision.
- Add regime probability and regime quality to diagnostics/dashboard.
- Measure performance of every existing filter inside each regime.
- Allow future thresholds to adapt by regime only after shadow validation.

## Phase 3 — Entry Timing AI

Goal: separate **direction** from **when to enter**.

Work:
- Direction model: BUY / SELL / neutral.
- Entry model: ENTER NOW / WAIT / PULLBACK.
- Learn from short-horizon outcomes and trade entry delay.
- Measure late entries, premature entries and missed entries.
- Use Intrabar as one feature instead of treating it as an unquestioned hard rule.

## Phase 4 — Multi-Horizon Forecasting

Goal: understand whether short, medium and longer horizons agree.

Horizons:
- 1–3 minutes
- 5–15 minutes
- 15–30 minutes

Work:
- Produce directional probability and expected move for each horizon.
- Detect pullback-vs-trend situations.
- Distinguish short-term reversal risk from medium-term trend continuation.
- Add horizon agreement/disagreement to the decision audit.

## Phase 5 — Adaptive Exit / TP1 / TP2 / TP3

Goal: let exit logic adapt to market structure and continuation probability.

Work:
- Predict probability of reaching TP1, TP2 and TP3.
- Estimate continuation probability after TP1 and TP2.
- Learn when partial close is better than full close.
- Add adaptive breakeven and trailing behavior in shadow mode first.
- Compare fixed exits against AI-assisted exits using realized R and net profit.

## Phase 6 — Confidence Calibration

Goal: convert raw model scores into historically meaningful probabilities.

Work:
- Calibrate direction confidence.
- Calibrate entry confidence.
- Measure reliability curves.
- Replace misleading raw edge interpretation with observed success likelihood.
- Display calibrated confidence only after sufficient sample coverage.

## Phase 7 — Ensemble Intelligence

Goal: combine complementary models instead of relying on a single forecaster.

Candidates:
- Chronos-2
- TimesFM
- XGBoost / LightGBM style tabular model
- Market-structure / microstructure model

Work:
- Score each model independently.
- Learn weights from recent held-out performance.
- Prevent one weak model from dominating the vote.
- Compare static weights vs regime-aware weights.
- Keep promotion controlled by holdout performance.

## Phase 8 — News and Session AI

Goal: adapt Ramon to context outside pure price direction.

Work:
- Learn impact of event type, country, importance and time-to-event.
- Model Asia / London / New York behavior separately.
- Measure spread and volatility changes around sessions/news.
- Learn when news should reduce confidence rather than simply block trading.

## Phase 9 — Risk AI

Goal: scale risk according to measured setup quality without exceeding hard safety limits.

Work:
- Risk multiplier from calibrated confidence, regime and volatility.
- Keep hard max executable risk as a non-learned safety boundary.
- Compare fixed risk vs adaptive risk in simulation/shadow mode.
- Never let learned risk controls bypass account-level caps.

## Phase 10 — Threshold Optimization

Goal: replace guess-based thresholds with evidence.

Candidate thresholds:
- minimum strength
- minimum edge
- trend path ATR
- trend consistency
- intrabar move/rebound
- spread limits by regime/session

Method:
- Walk-forward validation.
- Optimize for net expectancy / R, not only win rate.
- Penalize excessive false blocks and overtrading.
- Do not tune on the same sample used for evaluation.

## Phase 11 — Counterfactual Analysis

Goal: answer “what would have happened if Ramon had made a different choice?”

Examples:
- What if AI Trend had not blocked this entry?
- What if entry happened 3 or 5 minutes later?
- What if TP1 had partially closed the position?
- What if the trade was skipped because confidence was low?

Use:
- Diagnose hard filters.
- Measure opportunity cost.
- Compare candidate policies without changing live trading.

## Phase 12 — Self-Audit

Goal: make Ramon explain where its decision pipeline is helping or hurting.

Daily/periodic report:
- Direction accuracy
- Forecast accuracy
- AI Trend accuracy
- Intrabar accuracy
- False Entry rate
- False Block rate
- Net expectancy
- Profit factor
- Average R
- Performance by regime
- Performance by session
- Top blocking reason
- Top loss-causing decision component

## Phase 13 — Controlled Online Learning

Goal: learn from new data without destabilizing live execution.

Rules:
- No direct live self-modification.
- Train candidate models separately.
- Validate on purged/held-out windows.
- Compare candidate vs active baseline.
- Promote only if predefined checks pass.
- Preserve rollback checkpoints and model manifests.
- Log every promotion and rollback.

## Target architecture

Ramon should evolve toward three coordinated intelligence layers:

1. **Direction AI** — Which side is favored?
2. **Entry AI** — Is now a good time to enter?
3. **Exit AI** — When and how much should be closed?

Supporting layers:
- Regime AI
- Confidence calibration
- News/session context
- Risk AI
- Multi-model ensemble
- Self-audit and counterfactual evaluation

## Success metrics

A change is considered useful only when it improves the decision system on held-out data. Track:

- Direction accuracy ↑
- False Entry rate ↓
- False Block rate ↓
- Average R per trade ↑
- Net expectancy ↑
- Profit factor ↑
- Drawdown behavior stable or improved
- Calibration error ↓

Win rate alone is not an acceptance criterion.
