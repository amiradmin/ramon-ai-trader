# Stage 2 — Redis Streams buffer

Use separate streams and consumer groups for market data, execution events and
low-priority telemetry. Configure persistence, memory limits and alerts for stream
length, consumer lag, pending entries, retry count and memory. Acknowledge only after
the durable DB transaction succeeds. Reclaim stale pending entries. After bounded
retries, send poison records to a dead-letter stream with a reason. Never silently
trim unprocessed execution events; retention must cover outages and be backed by DB idempotency.
