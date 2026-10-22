# Stage 6 — durable order intent and EA protocol

Persist approved order intent before exposing it to the EA, ideally through a DB
transaction and transactional outbox. Use unique intent IDs and deliver only intents
assigned to the authenticated EA instance. Report received, submitted, filled, partially
filled, rejected and closed outcomes with stable event IDs and broker ticket/deal IDs.
Repeated polling must not create duplicate broker orders. Add expiry and MT5 reconciliation.
Do not enable this path until demo-account integration tests pass.
