# Live adverse intrabar entry guard

Ramon now delays a **strong Chronos BUY/SELL** when the current micro-bar move is
materially against the dominant Chronos direction.

## Live rule

A strong Chronos entry is held at WAIT when:

- the normal strong forecast edge/strength requirements are already met;
- live micro-bars are present; and
- `intrabar_move_atr < -0.03`.

The response reason is:

```
adverse_intrabar_timing
```

The decision payload also exposes:

- `strong_entry_min_intrabar_move_atr`
- `strong_entry_guard_active`

The guard affects only the strong `forecast_up` / `forecast_down` path.
Existing intrabar-reversal and AI-trend-continuation paths keep their own
confirmation rules. Risk sizing, SL, TP, profit protection and EA execution code
are unchanged.

This rule was introduced from the Selection Failure Lab evidence showing that
Ramon-selected holdout bars had materially more adverse intrabar movement than
nonselected bars. The threshold is intentionally tolerant of small noise:
moves down to -0.03 ATR are still allowed.
