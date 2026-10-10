# Ramon Tester update — 2026-10-10

This is an **isolated MT5 test build**, not production V2.

## Changes

1. `scripts/generate_ramon_tester.py` removes Win32 clipboard DLL imports
   and replaces `CopyDiagnosticToClipboard` with a disabled stub.
2. The generated tester build now also defaults
   `WriteDiagnosticFile=false`. This avoids the previously observed
   `Ramon diagnostic write failed err=5004` in the tester agent.
   It does not change the source of the production Ramon EA.
3. `src/ramon/v2_tester_signal_replay.py` audits **persisted V2
   decisions only**, enforcing an as-of cutoff and excluding WAIT, stale,
   future, or incomplete signal bars. It does NOT execute orders or
   calculate profit. Signal-bar timestamps must be verified against actual
   recorder semantics before using the accepted count as a quality measure.

## Commands (HP-Mini)

```bash
cd ~/Documents/Presentation/ramon-ai-trader
git pull --ff-only
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_generate_ramon_tester.py \
  tests/test_v2_tester_signal_replay.py
python3 scripts/generate_ramon_tester.py

TESTER="$HOME/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/RamonTester"
mkdir -p "$TESTER"
cp -v build/mt5-tester/RamonTester.mq5 "$TESTER/"
grep -nE '#import|input bool WriteDiagnosticFile' "$TESTER/RamonTester.mq5"

# Read-only saved signal audit (don't claim broker-quality fills)
PYTHONPATH=src .venv/bin/python -m ramon.v2_tester_signal_replay \
  --db data/ramon_history.sqlite3 --start-utc 1791331200 \
  --end-utc 1791590400 --output data/v2_replay_asof_oct07_09.json
```

Then open **only** `RamonTester.mq5` in MetaEditor,
press F7, require zero errors, and verify the **RamonTester.ex5**
timestamp before running the Oct 7–9 Strategy Tester.

Set tester EA `EnableLiveTrading=false`. Inspect its
Journal after completion for DLL/diagnostic errors. No changes to
`Ramon.ex5` are required.

## Remaining blocking integration

MetaTrader Strategy Tester **does not allow WebRequest**. The current
Ramon V2 EA obtains AI signals over HTTP. The standalone saved-signal
audit is *not* integrated into the MQL5 Strategy Tester execution loop,
so it cannot validate Guardian entry rejection or broker PnL.

To validate the two overlapping 0.39-lot SELL orders, a **tester-side
recorded-decision bridge** and explicit broker-tick execution harness
must be implemented and tested. Do not arm real-money trades to work
around this gap.
