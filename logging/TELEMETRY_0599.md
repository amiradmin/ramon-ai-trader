# Ramon 0.59.9 research telemetry (experimental)

Branch: `feature/research-telemetry-0599-20261013`; based on 0.59.8.
No production installation or MetaEditor compilation has been performed.

`decision_gate` captures a validated BUY/SELL/WAIT and model reason *before*
later risk and execution checks; it must not be interpreted as the final
execution approval. `order_intent`/`order_result` instrument the regular
entry route, including dashboard manual entries, but not yet every guardian
or auxiliary route. Dashboard manual close records intent/result; broker
deal close records the realized execution and its reason. Broker
`expert_or_api` is not proof of autonomous Ramon execution. Sampled stop
changes cannot establish that no manual change occurred between observations.

## Verify in isolated demo terminal

```bash
cd ~/Documents/Presentation/ramon-telemetry-test
git fetch origin feature/research-telemetry-0599-20261013
git switch -c telemetry-0599 --track origin/feature/research-telemetry-0599-20261013
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --with pytest python -m pytest -q tests/test_research_telemetry_static.py tests/test_research_telemetry_0599.py
```

Copy **both** `mt5/Ramon.mq5` and `mt5/RamonResearchTelemetry.mqh`
into a separate demo MetaEditor directory. Compile F7 with zero errors.
Verify WAIT decisions, simulated/demo entry/exit, manual dashboard close,
JSONL parseability and PostgreSQL ingestion. Do not replace the live EX5.
Use `logging/telemetry_quality_0599.sql` for read-only database audits.
