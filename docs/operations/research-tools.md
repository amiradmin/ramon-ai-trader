# Ramon research tools

## Installed skill

The versioned skill is [`skills/ramon-research/SKILL.md`](../../skills/ramon-research/SKILL.md).
This workstation has a symlink from `~/.codex/skills/ramon-research` to that directory.
Use `$ramon-research` in a new Codex chat when researching a policy or forecast.
The skill is a workflow for the coding assistant, not executable trading logic.

## LONA experiments

`research/lona/` contains the complete source uploaded to the connected LONA
plugin. Each source passed its complete-code dry run before a new versioned
strategy was created. Reports and exact settings are recorded under `docs/research/`.

These are simplified EMA trend and EMA pullback comparators, **not ports of the
full Ramon SignalFlow engine**. Both use 20/50 EMA direction, a 10% target exposure,
close-evaluated 2 ATR adverse / 3 ATR favorable exits, an opposite-trend exit, and
an exit signal after 24 holding bars. All market orders execute on the next bar
open, including exits; this is not an intrabar stop-order simulation. Integer
Backtrader sizing can make actual exposure lower than the requested percentage.

Before rerunning, retrieve the LONA authoring documentation, inspect the broker
symbol's coverage, and dry-run the full source. Run both candidates on identical
periods, cash, commission and exposure. No parameter optimization is part of this
experiment. LONA's exposed runner does not accept spread/slippage settings; do
not describe commission-only results as realistic broker-net results. The
provider's aggregate statistics take precedence over gross reconstructed trade
P&L. Record any positions still open at the evaluation boundary.

For the actual full policy use `manage.py evaluate_demo_playbook --help` and the
existing [playbook report](../research/demo-playbook-validation-2026-11-09.md).
Its R results and different account sizing are not directly comparable with the
LONA percentage returns.

## Economic calendar

The connected TipRanks tool can retrieve US High/Medium events. It runs in the
assistant session; the Django container cannot call that connector automatically.
The dashboard therefore consumes a validated, timestamped **imported snapshot**,
not a live subscription. No API credentials are copied into the repository.

Save the connector result in this envelope, preserving the actual retrieval time:

```json
{
  "provider": "TipRanks",
  "retrieved_at": "2026-11-09T09:00:00+00:00",
  "from_date": "2026-11-09",
  "to_date": "2026-11-16",
  "payload": {
    "totalMatched": 0,
    "returned": 0,
    "economicCalendar": []
  }
}
```

Use the actual unmodified payload, not the empty example, for an import. Retrieve
the full chosen range (maximum 200 events); truncated results are rejected.

```bash
docker cp /tmp/ramon-economic-calendar.json ramon-django:/tmp/ramon-economic-calendar.json
docker compose exec -T django python manage.py import_economic_calendar /tmp/ramon-economic-calendar.json
```

The importer writes atomically into `MEDIA_ROOT/research/economic-calendar.json`,
which is persisted in the existing `learning_models` volume. The GET endpoint is
`/api/ea/v1/research/economic-calendar`. Dashboard polling rereads the imported
file; it does not refresh the provider. After 24 hours or after coverage ends,
the panel marks the snapshot stale. A missing or corrupt file has an explicit
unavailable state. Failed imports preserve the last valid snapshot.

Times with an explicit offset can be converted to UTC. The current connector
response has naive times, so the panel labels its timezone unverified and shows
the original strings. Do not append `Z`, assume the host timezone, calculate a
countdown, or feed these rows into the trading news gate. Future automatic runtime
refresh requires a separately configured provider API and a verified timezone.
This integration is display-only and does not write decisions, thresholds or
broker commands.
