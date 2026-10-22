# Stage 5 — independent risk gate

Risk is a mandatory boundary, not just a confidence threshold inside strategy. Validate
account state, open exposure, spread, stop distance, lot limits, session constraints,
symbol contract settings and market-data freshness. Return approved size/protective
levels or a stable rejection reason. Missing account data, stale prices or exceptions
fail closed. EA-local stop and position-management controls stay active regardless of Django availability.
