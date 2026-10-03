# Decision validation and daily evaluation repair — 2026-10-03

- Live requests validate M1 timestamps against broker quote time, rejecting future, stale, missing-clock and non-contiguous contexts. Forming M1 is supported because `BuildRequest` sends `CopyRates(...,0,4,...)`.
- Historical evaluation uses four actual completed M1 candles immediately before the M15 closing boundary. No synthetic M1 or relaxed direction confirmation is introduced.
- Daily training waits before loading/training models when evaluation coverage or recorded spreads are insufficient. Training and both evaluations share one immutable M15 snapshot.
- Replay skips discontinuous future M15 horizons before inference. Daily evaluation requires recorded spreads and adds a configurable, assumed 0.1R round-trip cost above spread.
- This coarse replay excludes live EA exits, intrabar timing and RANGE. It remains research evidence only. Daily script does not automatically promote Chronos.

## Read-only deployment check

The running `/health` endpoint reports ready, Chronos-2-small, role mode shadow, inactive ensemble and enabled direction confirmation. It does **not** expose the `market_state_policy_enabled` or `replay_inputs_enabled` fields present in repository source. Therefore the running service cannot be treated as matching this checkout. Its exact Git revision is not exposed.

The latest EA diagnostic reports version 0.54.8; repository source reports 0.54.9 (MQL property 1.549). The diagnostic was over 30 minutes old and reported Stale tick. This is evidence about the last recorded state, not proof of the current loaded EA version.

No server restart, deployed EA replacement, live threshold changes or account-loss-limit activation were performed.

## Current data coverage

Read-only check: 10,525 M15 bars, 14 complete M1 boundary contexts in the holdout, only 4 eligible stride-4 evaluation contexts versus minimum 20, and 18 missing holdout spreads. `ramon.daily_train` returns `waiting_for_evaluation_context` before training. Recorded quote-level context remains preferable for faithful live-policy research; collecting continuous M1 and spreads is necessary for this boundary replay.
