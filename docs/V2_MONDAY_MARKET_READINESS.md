# Ramon V2 — Monday 12 October 2026 deployment checklist

This is a **staged deployment**, not an unattended promise to start trading.
Market opening depends on LiteFinance's XAUUSD_l broker session hours. It
cannot be inferred from a local wall clock; confirm **Market Watch** / session
specification and a fresh Bid/Ask in the actual terminal.

## Before the weekend ends (while markets are closed)

```bash
cd ~/Documents/Presentation/ramon-ai-trader
git status --short                    # STOP if unexpected edits; do not delete
git fetch origin
git switch feature/v2-eight-capability-redesign
git pull --ff-only
uv sync --extra dev
bash scripts/v2_market_preflight.sh
bash scripts/setup_local.sh --diagnose
```

- Read the full preflight output. A PASS only proves the automated subset:
  **never live trading readiness**.
- `EnableLiveTrading=false` is the default. Do not alter it because
  Python tests pass.
- `scripts/setup_local.sh --mt5-only` automatically copies the EA to
  the active terminal data directory and invokes Wine MetaEditor. It
  **can overwrite the .ex5 used by the terminal**. Before invoking it,
  ensure MT5 is not trading, back up the existing `Ramon.mq5`,
  `Ramon.ex5`, settings and broker account logs outside the repo,
  and check `Ramon.log` after compilation. Do not trust a zero shell
  exit alone: require zero compile errors and confirm the expected file.
- An alternative is to copy `mt5/Ramon.mq5` to a separate test-terminal
  profile, compile and exercise that first, leaving the live compiled EA intact.

## Minimal validation matrix (must all pass)

1. Compile new MQL5 source on the *actual installed* MetaEditor: zero errors;
   record the log and SHA of deployed `.ex5`.
2. Strategy Tester or demo verify both entry paths (standard and Guardian):
   risk budget 300 units; first SELL consumes ~287.82 units; second SELL
   requesting ~292.50 units **must be blocked**.
3. With one unrelated-symbol position lacking SL, new entries fail closed.
4. With a pending order that consumes the cap, new entries fail closed.
5. Broker Stop Out generates a terminal global lock; restart preserves lock.
6. Closing an existing position remains possible under entry lock.
7. Successful model health plus valid quotes, spread, margin and news guard.
8. Check configured `MoneyUnitsPerUSD` against live account; verify
   dashboard's dynamic `MaxExecutableRiskUSD` cannot exceed portfolio cap
   and no manual control bypasses the guard.
9. Never run duplicate EA instances without testing simultaneous entry races:
   current preflight is **not an atomic broker-side account reservation**.
10. Ensure all eight integration states are described honestly: most
    remain partial or shadow; do not use unverified adapters for orders.

## At Monday broker open

- Check actual broker session status, not just local clock.
- Run `bash scripts/v2_market_preflight.sh` once, then verify MT5
  **Experts** and **Journal** show connected account and no compilation
  or runtime errors.
- Only after completed demo/tester matrix and explicit account-risk
  approval should the operator arm a live EA. Avoid increasing trading
  risk to chase previous losses.
- If any check fails, **leave new entries disabled**. Keep broker-side
  protective SL/TP and independently inspect existing positions.

## Rollback

- Turn new entries off in EA settings; never forcibly close managed
  positions as part of a deployment rollback.
- Restore backed-up known-working EA binaries and .set through the
  controlled MT5 install procedure; keep logs and trade database.
- Do not run `git reset --hard`, `git clean`, `docker compose down -v`,
  or delete MT5 terminal globals without diagnosing ongoing exposure.
- Record the branch, commit, binary SHA, broker account currency scale,
  health report timestamp and risk parameters.
