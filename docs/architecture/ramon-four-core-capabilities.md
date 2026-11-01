# Ramon: Four Core Trading Capabilities

## Purpose

This document records Ramon's agreed high-level architecture: four connected capabilities that form a complete trading and continuous-improvement cycle.

1. Identifying the right opportunities.
2. Entering trades at the right time.
3. Managing risk precisely.
4. Learning from trading outcomes.

The fourth capability operates in **Level 1 — Assisted Learning**. Ramon may analyze outcomes and recommend improvements, but a human must approve any proposed change before it can enter a separate implementation and validation workflow.

## Architectural authority

The existing command architecture remains authoritative:

- **Django is Ramon's authoritative brain.** Strategy evaluation, opportunity decisions, entry decisions, risk verdicts, trading authorization, and command creation remain server-side.
- **The MT5 Expert Advisor is a telemetry and execution bridge.** It reports broker/account/market information and executes only commands authorized and returned by Django. It must not contain a second strategy or risk engine.
- **The learning subsystem is analytical and advisory.** It is not a trading decision engine and cannot create, authorize, or execute orders.
- **Existing SignalFlow, OpportunityDetector, Risk Management, APIs, and command safety gates must remain behaviorally unchanged while learning is introduced.**

The learning subsystem must be isolated from the trading decision and execution path. If learning is unavailable, existing trading services continue with their existing behavior and safety controls.

## Capability 1 — Identifying the right opportunities

Ramon evaluates market structure and available evidence to identify valid trading opportunities.

Responsibilities include:

- Evaluate completed market data and causal indicators.
- Identify market regime and relevant structural conditions.
- Detect eligible setups and record why they qualify or fail.
- Preserve the evidence and conditions behind each opportunity.
- Avoid look-ahead bias and use consistent UTC timestamps.

This capability must continue to use the existing strategy and SignalFlow implementation. The learning system may later analyze its outcomes, but must not silently change its rules.

## Capability 2 — Entering trades at the right time

Ramon evaluates whether an identified opportunity has reached a valid entry point.

Responsibilities include:

- Evaluate entry timing and confirmation conditions.
- Record satisfied, waiting, and blocked conditions.
- Preserve the exact decision-time snapshot.
- Produce an auditable Django decision and, only when all existing authorization gates permit, an eligible command.
- Keep execution on the MT5 bridge limited to Django-authorized commands.

A detected opportunity is not, by itself, permission to enter a trade.

## Capability 3 — Managing risk precisely

Django remains responsible for risk evaluation and authorization.

Responsibilities include:

- Validate account and daily-risk baselines.
- Evaluate position sizing and monetary exposure.
- Validate stop-loss, take-profit, and risk/reward constraints.
- Enforce existing risk limits and fail-closed behavior.
- Record the risk verdict and its supporting evidence.

The learning subsystem is read-only with respect to live risk configuration. It cannot change risk limits, sizing rules, stop-loss rules, take-profit rules, or authorization gates.

## Capability 4 — Learning from trading outcomes

### Operating mode: Level 1 — Assisted Learning

The learning subsystem reviews completed broker trades, compares actual outcomes with the original Django decisions, discovers recurring patterns, and creates evidence-backed improvement proposals.

**The learning system recommends. The human approves. Django remains the authoritative trading brain. MT5 remains the execution bridge.**

### Learning workflow

1. **Outcome collection:** Reconstruct the complete broker trade lifecycle and link it to the original Django decision, risk verdict, and order intent where available.
2. **Trade analysis:** Calculate realized results, costs, R-multiples, Maximum Adverse Excursion (MAE), and Maximum Favorable Excursion (MFE).
3. **Performance analysis:** Evaluate results across direction, setup, market regime, volatility, entry conditions, session, and exit reason.
4. **Pattern discovery:** Identify recurring conditions using minimum sample sizes, baseline comparisons, and statistical uncertainty.
5. **Improvement proposals:** Produce recommendations with supporting evidence, limitations, expected benefits, possible negative effects, and a proposed validation plan.
6. **Human review:** A reviewer accepts a proposal for further backtesting, rejects it, or requests more evidence.
7. **Separate validation:** An accepted proposal may proceed to controlled research/backtesting and demo validation. It is not applied to live trading automatically.

### Learning cadence

- **Trade-level:** Analyze each trade after its broker lifecycle is complete.
- **Daily:** Summarize recent performance and detect operational or execution anomalies; do not infer strategy changes from a small short-term sample.
- **Weekly:** Evaluate sufficiently large samples, compare market regimes, and generate statistically supported proposals.

Learning tasks must run outside the trading decision and execution path. Their failure must not block trading services.

### Data and evidence requirements

Use existing Ramon records wherever possible; inspect current models before adding new ones. Reuse and link existing records such as StrategyDecisionRecord, RiskVerdictRecord, and OrderIntentRecord where they exist.

The logical learning records may include:

- **TradeOutcome:** Final broker lifecycle, position identity, aggregated deals, realized P&L, commissions, swap, spread, slippage, and exit information.
- **TradeDecisionSnapshot:** Immutable decision-time market and strategy evidence.
- **TradeAnalysis:** Reproducible metrics calculated from the completed outcome.
- **DiscoveredPattern:** A pattern, its sample, baseline, uncertainty, and supporting analysis.
- **ImprovementProposal:** A proposed change, evidence, expected benefits, risks, and validation plan.
- **LearningReview:** Human decision, reviewer, timestamp, and comments.

Do not create duplicate history or decision records if existing models already provide the required source of truth.

Trade collection must be idempotent, handle partial closes and multiple broker deals correctly, and distinguish positions from orders and deals. Preserve the original decision snapshot; never rebuild it using future information.

### Performance measures

At minimum, define and test:

- Win rate.
- Profit factor.
- Expectancy.
- Average realized R-multiple.
- Drawdown.
- MAE and MFE.
- Holding duration.
- Gross and net P&L.
- Commission, swap, spread, and slippage.

Metric formulas, denominators, treatment of breakeven trades, and incomplete data must be explicit and consistently applied.

Pattern analysis must include configurable minimum sample requirements, uncertainty/confidence intervals where appropriate, and comparisons against a relevant baseline. Do not present correlation as causation or small samples as reliable evidence.

### Human review states

Use explicit, auditable proposal states:

- PENDING
- ACCEPTED_FOR_BACKTEST
- REJECTED
- NEEDS_MORE_EVIDENCE

Accepting a proposal only authorizes further research and validation. It does not authorize a production configuration change.

## Non-negotiable safety boundaries

The learning subsystem may:

- Read historical trades and decision evidence.
- Calculate and store analytical metrics.
- Discover and report patterns.
- Generate recommendations.
- Prepare candidates for offline historical backtesting.
- Record human review decisions.

The learning subsystem must never:

- Modify live strategy thresholds or entry conditions.
- Modify live risk limits, position sizing, SL/TP rules, or authorization settings.
- Create or authorize trading commands.
- Call MT5 execution functionality.
- Deploy or activate a strategy automatically.

Enforce these restrictions in application/service permissions and data access, not only in dashboard controls. Learning must have no write path to live trading configuration or command-creation services.

## Delivery sequence

Implement incrementally, inspecting the actual repository before each change:

1. **Trade outcome infrastructure:** Trace existing telemetry and broker deal lifecycle; reuse existing records; implement correct aggregation, idempotency, and decision linkage.
2. **Performance analytics:** Implement and independently test metric formulas, costs, MAE/MFE, and realized R.
3. **Pattern discovery:** Add regime/setup comparisons, minimum sample rules, baseline comparisons, and statistical uncertainty.
4. **Improvement proposals:** Add evidence-rich proposals, review states, audit history, and strict permission boundaries.
5. **Dashboard integration:** Add learning overview, trade reviews, pattern analysis, strategy evaluation, proposals, and review history to the existing Ramon dashboard. Link each completed trade to its original Decision Process Flow.

Do not implement all phases blindly in one change. Test and verify each phase before proceeding. Preserve existing dashboard features, APIs, strategy behavior, and deployment conventions.

## Acceptance criteria

- [ ] Every completed broker position can be reconciled to its broker deals and linked to its original Django decision where the source evidence exists.
- [ ] Partial closes, commissions, swaps, spread, and slippage are handled consistently.
- [ ] Outcome collection is idempotent.
- [ ] Original decision-time evidence is immutable and free of look-ahead bias.
- [ ] Metric calculations have documented formulas and independent tests.
- [ ] Pattern findings show sample size, baseline, and statistical uncertainty.
- [ ] Proposals contain evidence, limitations, and a validation plan.
- [ ] Human reviews are auditable.
- [ ] No learning component can modify live configuration or issue trading commands.
- [ ] Existing SignalFlow, OpportunityDetector, Risk Management, API behavior, and command safety gates remain unchanged.
- [ ] Learning service/task failure cannot interrupt trading operations.
- [ ] New tests pass and deployment/migration requirements are documented.

## Final architectural statement

**Ramon identifies the right opportunities, enters at the right time, manages risk precisely, and learns from trading outcomes.**

The first three capabilities operate the trading system. The fourth evaluates evidence and recommends improvements under human supervision. Django remains the sole authority for trading decisions and authorization; MT5 remains the telemetry and execution bridge.
