# Ramon V2: MT5 terminal validation (no live trades)

This checklist is for an operator with access to the active HP-Mini / Wine
MetaTrader terminal. A normal CI run cannot establish native MT5 execution
correctness.

## 1. Verify installed compiled artifact

```bash
cd ~/Documents/Presentation/ramon-ai-trader
git fetch origin
git switch feature/v2-eight-capability-redesign
git pull --ff-only
python3 scripts/verify_v2_mt5_deployment.py
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_v2_account_risk.py tests/test_v2_mt5_ea_wiring.py \
  tests/test_verify_v2_mt5_deployment.py
```

The verifier checks matching source SHA and EX5 file timestamp. It **cannot**
prove how an EX5 was built. Record SHA256 and MetaEditor successful build log.
`EnableLiveTrading` must stay false for the initial validation.

## 2. MT5 Strategy Tester setup

1. Open the installed terminal (Wine) and press **Ctrl+R**.
2. Select **Expert Advisor** `Ramon\Ramon`; verify the terminal actually
   uses the newly compiled EX5 via the expected data directory.
3. Symbol **XAUUSD_l**, timeframe **M15**.
4. Select **Every tick based on real ticks** if broker tick data is
   available. A fallback tick mode is **not** equivalent evidence.
5. Choose a historical range that includes **2026-10-07 and 2026-10-08**
   if broker history is available, plus an independent later interval.
6. For the **first test**, keep `EnableLiveTrading=false`. This
   validates load and model-failure behavior only, not entry gate behavior.
7. Enable visualization/logs; record terminal Journal, Experts and
   backtest report.

### Critical limitation: HTTP AI decisions

Native MT5 Strategy Tester **does not execute WebRequest**. Since Ramon V2's
decision endpoint is `http://127.0.0.1:8012/decision`, running the EA as-is
in the native tester will generally **not reproduce its actual AI entry
signals**, and cannot honestly validate the normal/Guardian risk pathways.

A deterministic tester-compatible **recorded-signal replay adapter** or
a separate demo-test harness is required to exercise the entry guard in
real execution. Do not enable a live account to make a tester pass.

## 3. Mandatory demo-only scenarios once replay/harness exists

- A 0.39-lot SELL with risk 287.82 account units; attempting another
  0.39-lot SELL with risk 292.50 units must be rejected when the
  portfolio cap is 300 units.
- A position from a different Magic or instrument counts toward the
  portfolio risk budget.
- A pending order on a different symbol counts toward the total risk.
- Missing SL on any existing exposure rejects new entries.
- A stop-out deal sets a persistent terminal lock; restart cannot
  silently remove the lock. Review its manual release separately.
- Existing position closes must still work while entries are locked.
- Guardian reverse-entry path must check risk **after** the original
  close is confirmed.
- Margin projected below the configured floor rejects the entry.
- Broker minimum lot, spread spikes, missing model snapshots and stale
  exchange quotes produce no new orders.
- Verify release happens only with correct broker account, volume unit
  conversion and explicit operator approval.

## 4. Readiness decision

**Ready to load/observe:** MetaEditor compile, Python tests and EX5 audit pass.

**Ready for real automatic entries:** NOT YET. Native replay or demo evidence
must show the actual EA rejecting dangerous orders, and operator must
approve the account-level caps in the actual cent-account denominations.

A failed native tester due to unsupported `WebRequest` is an
integration limitation, not a reason to bypass the risk gate.
