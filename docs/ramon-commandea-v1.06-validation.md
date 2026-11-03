# Ramon Command EA v1.06 validation

## What changed
- Adds timer-driven, retryable delivery of fully closed hedging positions to `POST /api/ea/v1/learning/outcomes`.
- Aggregates position-linked broker profit, swap, commission and fee into net realized PnL.
- Includes account/instance/symbol, opening-order ticket, timestamps, side and exit reason.
- Persists a per-account/per-instance deal cursor in MT5 terminal Global Variables.
- Keeps Django as the only strategy/decision/learning authority. The EA only reports broker facts.
- Does not invent MAE, MFE, risk amount, slippage or entry/exit efficiency when broker history cannot provide them.

## Before installing
1. Confirm the MT5 account is the intended demo account and the chart is the intended symbol/timeframe.
2. Keep Django command creation/execution settings at their existing safe values; this telemetry change does not require enabling order execution.
3. Set the bearer token only in local MT5 EA Inputs. Never commit it.
4. Add the Django API base URL to MT5 Tools → Options → Expert Advisors → Allow WebRequest for listed URL.
5. Confirm the EA instance ID is stable between restarts. The learning cursor is scoped to account login and instance ID.

## Build and deploy
Run the repository's pinned update/build script:

```bash
cd ~/Documents/Presentation/ramon-ai-trader-bridge
bash scripts/update_build_ramon.sh
```

The script compiles and installs only `RamonCommandEA_v1.06.mq5`. Confirm the fresh MetaEditor log reports exactly `0 errors, 0 warnings`, and that the generated EX5 is new and non-empty.

## Demo validation
1. Attach v1.06 to the intended demo chart with command execution left disabled.
2. Confirm ordinary telemetry and Django decision requests continue to work.
3. Open and close one small demo position through the existing authorized workflow (or use a known existing demo trade if appropriate).
4. Wait for the next EA timer cycle. The Experts log should report that the completed position was accepted by Django.
5. Open `/api/ea/v1/learning/overview` or the Learning Engine dashboard and confirm one outcome appears with:
   - a stable trade ID based on account login and position ID;
   - correct symbol and BUY/SELL side;
   - opening and closing timestamps;
   - net realized PnL that includes position-linked commission, swap and fees;
   - the correct decision link when the opening-order ticket is present in the command acknowledgement.
6. Confirm a repeated delivery returns an idempotent duplicate response and does not increase the closed-trade count.
7. Simulate a temporary API failure, close a demo position, restore connectivity, and confirm the EA retries without losing the event.
8. Restart the EA and confirm its terminal Global Variable cursor prevents unnecessary historical replay.
9. Confirm manual/unlinked trades are recorded without fabricated decision metadata.

## Known limitations
- The current implementation targets the project's hedging-account workflow. Netting reversal (`DEAL_ENTRY_INOUT`) is not split into separate logical outcomes.
- On first installation, the EA backfills up to 90 days of available broker deal history.
- If terminal Global Variables are cleared or MT5 is reinstalled, the cursor resets and the EA may re-send historical outcomes. Django idempotency prevents duplicate records.
- Historical open/close timestamps use the current broker UTC offset. A position spanning a broker daylight-saving transition requires broker-specific historical timezone evidence for exact timestamps.
- MAE, MFE, initial risk, spread-at-entry, slippage and efficiency metrics remain unavailable unless separately captured; the EA does not infer them from final PnL.
- The EA reports completed broker positions only. It does not generate signals, change strategy parameters, or bypass Django authorization.

## Acceptance
Do not treat source guards, CI success or MetaEditor compilation as proof of safe trading. Accept this integration only after the demo validation above confirms the payload, net PnL, decision attribution, duplicate handling and retry behavior. Keep live trading disabled.
