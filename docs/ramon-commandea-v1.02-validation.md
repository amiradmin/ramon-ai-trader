# RamonCommandEA v1.02 validation checklist

## Scope

This version adds diagnostic output for decision polling and successful Django decision HTTP responses. It does not change the Django decision policy, risk gates, command payload, or broker execution behavior.

Safety defaults must remain:
- `EnableCommandExecution=false`
- Django remains the authoritative decision-maker.
- Telemetry must send `server_name` and broker-local `broker_day` (`YYYY-MM-DD`); the decision request must send the same `server_name` so Django can validate the account and use its server-side daily-risk baseline.
- Live-account order execution is not enabled by this diagnostic build.

## Build

1. Copy `mt5/RamonCommandEA_v1.02.mq5` to the active MT5 terminal's `MQL5/Experts` directory.
2. Open the versioned source in MetaEditor and compile it.
3. Confirm 0 errors. Verify `RamonCommandEA_v1.02.ex5` exists before attaching it.
4. In MT5, attach the versioned EA only to the intended demo `XAUUSD_o` M15 chart.
5. Set `DjangoBearerToken` locally in EA Inputs. Never commit or share the token.
6. Confirm `EnableCommandExecution=false`; do not enable command execution during diagnostics.

## Expected diagnostic sequence

- `RamonCommandEA v1.02 initialized in TELEMETRY-ONLY MODE`
- `RamonCommandEA v1.02: decision poll started` approximately every configured decision interval (minimum 30 seconds)
- On a successful HTTP response: `Django decision HTTP success code=...`
- On a valid JSON decision response: `RamonCommandEA Django decision: ...`

HTTP failures should include the endpoint path, status/error code, and the existing generic decision-request failure message. A skipped decision should report its existing guard reason, such as wrong timeframe, insufficient completed bars, or invalid quote.

## Acceptance checks

- [ ] MetaEditor reports 0 compilation errors and 0 warnings.
- [ ] Versioned source and EX5 exist and match the v1.02 release name.
- [ ] EA reports telemetry-only mode.
- [ ] Decision poll starts at the expected interval.
- [ ] HTTP status and Django decision/reason can be correlated in the Experts log.
- [ ] Django continues to return `WAIT` whenever account state, identity, telemetry freshness, or server-side risk baselines are unverified.
- [ ] No broker order is placed during this test.
