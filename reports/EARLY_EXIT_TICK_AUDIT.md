# Early Exit Tick Audit (research-only)

CLI (run on HP-Mini against the **test** SQLite after importing M1):

```bash
uv run python -m ramon.early_exit_tick_audit \
  --db /tmp/ramon_m1_import_test.sqlite3 \
  --ticks-dir "$HOME/.mt5/drive_c/users/amir/AppData/Roaming/MetaQuotes/Terminal/Common/Files" \
  --lookahead-minutes 60 > /tmp/ramon_early_exit_tick_audit.json
uv run pytest -q tests/test_early_exit_tick_audit.py tests/test_early_exit_replay_validator.py
```

In the dashboard at http://127.0.0.1:8013/monitor click
**ممیزی تیک‌های پس از خروج** or navigate to
http://127.0.0.1:8013/early-exit-tick-audit . JSON:
`/api/early-exit-tick-audit`.

Dashboard reads **the live SQLite read-only**, which does not yet include the
M1 research imports. It can still inspect raw historical tick exports
because the MT5 user files are mounted at `/mt5-users`. The configured
default is `/mt5-users/amir/AppData/Roaming/MetaQuotes/Terminal/Common/Files`;
override with `RAMON_EARLY_EXIT_TICKS_DIR` if different.

Bid is used to observe closing a long (BUY); Ask to observe covering a
short (SELL). The first recorded quote at/after the actual close time is a
**reference quote only** and is NOT a verified executable fill. Favorable,
adverse, and final movements are XAUUSD price movements, **not** account
currency, trade profit, or an execution policy outcome.

The observational continuity metric requires a quote within 10 seconds of
exit and end of window, maximum inter-tick gap 60 seconds, no malformed
quotes and no duplicate timestamps. This is a pragmatic diagnostics threshold,
not proof of tick completeness from the broker. Always inspect per-trade
quality. Incomplete files cannot justify a replay conclusion.

No model/EA modification, no automatic position action and no SQLite writes.
The existing Early Exit validator's broker initial SL/TP and fill/fees blockers
are deliberately preserved; output always says `NOT_EVALUATED`.
