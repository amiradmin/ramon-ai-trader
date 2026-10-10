# Ramon V2 — Guardian / Stop-Out / Manual Exposure validation

**Implemented in this branch** (not merged or enabled for live trading):

- `tests/test_v2_guardian_stopout_wiring.py` confirms source ordering:
  Guardian must close and confirm original parent, then invoke account-wide
  `V2Preflight`, then (only if allowed) submit the reversal.
- `scripts/generate_ramon_tester.py` now emits a **tester-only**
  `RAMON_TEST_LOCK` self-check alongside the previously passing synthetic
  `RAMON_TEST_RISK` case. It creates a terminal global lock, verifies
  `V2Preflight` rejects entry with `reason=stop-out lock`, then
  restores the previous global value or deletes the new test-only value.
  This only checks presence + rejection, NOT actual terminal process restart.
- `src/ramon/v2_manual_exposure_audit.py` separates historical manual
  and EA-origin trades when `entry_source` / `entry_magic` are captured,
  and measures overlap from **closed** trades. Missing provenance is
  explicitly UNKNOWN. A history report does NOT include all live positions,
  floating losses or pending orders.
- Existing MQL5 `V2Preflight` loops through **all positions regardless
  of Magic** and all pending orders, so manual positions with SL and valid
  instrument sizing are included; an unprotected open position blocks
  all new entries, including Guardian.

## Run safely on HP-Mini

```bash
cd ~/Documents/Presentation/ramon-ai-trader
git status --short
git pull --ff-only

PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_v2_account_risk.py \
  tests/test_v2_mt5_ea_wiring.py \
  tests/test_v2_guardian_stopout_wiring.py \
  tests/test_v2_manual_exposure_audit.py \
  tests/test_generate_ramon_tester.py

PYTHONPATH=src .venv/bin/python -m ramon.v2_manual_exposure_audit \
  --db data/ramon_history.sqlite3

python3 scripts/generate_ramon_tester.py
TESTER="$HOME/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/RamonTester"
cp -v build/mt5-tester/RamonTester.mq5 "$TESTER/"
```

Compile **RamonTester.mq5 only** with F7 / 0 errors / 0 warnings.
In Strategy Tester, leave `EnableLiveTrading=false` and set
`TesterRunRiskHarness=true`, cap inputs 3.0 (test only), currency scale 100,
`WriteDiagnosticFile=false`. Run Oct 7–9 on XAUUSD_l M15 and inspect Journal:

```text
RAMON_TEST_RISK PASS portfolio cap blocked additional SELL
RAMON_TEST_LOCK PASS persistent stop-out global blocks entry
```

If a case is FAIL or INCONCLUSIVE it is not approved.

### Missing acceptance tests (must precede any real-money enrollment)

1. **True persistence across restart**: set lock inside a disposable MT5
   tester/demo terminal, restart the same terminal, then call V2Preflight
   while the lock exists. The one-run harness above cannot prove this.
2. **Actual Guardian reversal on demo**: trigger post-close path using
   a recorded confirmed reversal, verify no orphan pending entries, and
   that both broker close result and risk cap are checked.
3. **Account aggregation in a live snapshot on demo**: another magic,
   symbol and manual position must count. Unknown-SL positions should
   fail closed. Preserve the user's historical manual trades for account
   reporting but never label them as losses of V2's AI.
4. **External MT5 order races**: multiple chart instances can interleave
   account reads and OrderSend; a preflight by itself is not an atomic
   reservation. Never infer this risk is eliminated.
5. **Stop-out reconstruction after downtime**: live `OnTradeTransaction`
   only sees events delivered while EA runs; scan historical deal records
   on startup before clearing or arming any lock.

**Do not replace the primary Ramon.ex5 or enable live orders from these
test results. PR #71 remains Draft.**
