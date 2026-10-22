# Stage 1 — MT5 EA ingestion

Authenticate the EA, enforce a strict body-size limit, validate a versioned JSON
envelope and stable event ID. Acknowledge only after the event entered the buffer
or durable store. Keep the request path free of inference and DB-heavy work.

Execution events require EA-generated IDs so retries are idempotent. Record server
receive time separately from EA occurrence time. Telemetry endpoints never grant
trade authorization. Reject requests when the bearer token is unset; compare tokens
in constant time and keep the service loopback-bound unless protected by TLS/network controls.
