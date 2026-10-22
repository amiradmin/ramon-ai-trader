# Stage 4 — strategy and decision record

Consume a consistent market snapshot and produce an immutable decision record:
decision ID, symbol, action, confidence, rationale codes, feature snapshot/reference,
model/policy versions and timestamp. HOLD is valid. Strategy never submits orders
directly. Keep Chronos and other model endpoints behind explicit adapters until parity
tests prove replacement behavior.
