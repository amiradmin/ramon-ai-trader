# Ramon V2 — next acceptance steps after manual/auto split

The 440 closed-trade report from 2026-10-10 was classified as:
- Explicit automated: 48, +3.84 account units
- Dashboard or human-assisted: 162, -834.51 account units
- Unknown entry source: 230, -47.73 account units

These are classifications of *recorded provenance*, not verified attribution of
all individual trades. Dashboard-origin is not necessarily the same as a manual
order placed in the MT5 terminal. Do not feed UNKNOWN, HUMAN_ASSISTED or
DASHBOARD_OPPORTUNITY rows into an AI-only supervised outcome evaluation
without independent entry-intent evidence. All their open positions and pending
orders **still count** toward live broker account risk.

## Immediate next test: true terminal restart (demo, no orders)

The single-run tester checks below have already been observed; do not rerun
them as evidence of restart persistence. Follow
`docs/V2_STOPOUT_RESTART_ACCEPTANCE_FA.md` and generate the isolated order-free
script with `python3 scripts/generate_stopout_restart_probe.py`.
Real restart, MQL compilation and downtime history recovery remain unverified.

## Existing terminal-global Stop Out latch check (tester only)

1. Run `git pull --ff-only`.
2. Run `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_generate_ramon_tester.py tests/test_v2_guardian_stopout_wiring.py tests/test_v2_manual_exposure_audit.py`.
3. Generate/copy the isolated source, compile **RamonTester only**, zero errors.
4. Select `RamonTester/RamonTester.ex5` in MT5 Strategy Tester.
5. Use `XAUUSD_l M15`, Oct 7–9, `Every tick based on real ticks`.
6. Inputs: `EnableLiveTrading=false`, `TesterRunRiskHarness=true`,
   `EnableV2AccountRiskGuard=true`, three `V2Max*RiskUSD=3.0`,
   `MoneyUnitsPerUSD=100`, `WriteDiagnosticFile=false`.
7. After completion review **Experts** and **Journal** logs for
   `RAMON_TEST_RISK`, `RAMON_TEST_LOCK` and `RAMON_TEST_GUARDIAN`.
8. Expected: all three are `PASS`; any missing/FAIL/INCONCLUSIVE is a blocker.\n   `RAMON_TEST_GUARDIAN` is a **dry-run risk veto** on hypothetical\n   post-close exposure, not a broker-side parent-close/reverse-fill test.

The `RAMON_TEST_LOCK` harness creates, checks and restores a terminal global
*in one tester execution*. It is NOT a restart-persistence test. A real
stop-out history reconstruction test is outstanding.

## Guardian demonstration (isolated demo account only)

Precondition: choose a demo account and broker/terminal where STOP and reverse
transactions can be observed. Back up the current test .set and logs. Do not
attempt this test on LiteFinance live.

- Confirm a Guardian test setup with an identified parent position,
  reproducible direction/reversal signals, account balance, lot size and SL
  before testing.
- Capture initial parent ticket, existing positions and margin.
- Demonstrate the **parent close confirmation before reverse submission**,
  with exchange of ticket/deal IDs in Experts/Journal.
- Demonstrate **no reverse order** when account-risk preflight rejects it,
  including when a manual/demo position consumes the portfolio cap.
- Confirm any failure keeps the account within pre-agreed exposure and
  leaves no orphan pending order.
- Save terminal Journal + deal history; evidence is necessary to claim PASS.
  The current source-order static test is only structural coverage.
- A full automated demo validation will need a dedicated offline signal
  injection/replay mechanism compatible with the tester (WebRequest disabled
  in native tester); this does not currently exist.

**Risk caution**: the live EA has not been recompiled from further MQL5
changes in this step; the real account must remain disarmed until demo
acceptance, persistent stop-out recovery and operator review are complete.
