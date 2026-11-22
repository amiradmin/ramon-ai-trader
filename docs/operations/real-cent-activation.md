# Real cent account activation

The MT5 command EA v1.09 reports the broker's account trade mode, account currency, login, server, symbol, tick size/value and volume limits. MT5 reports a cent account as `ACCOUNT_TRADE_MODE_REAL` (integer `2`); the real mode alone does not prove cent denomination. Verify the account currency in *MT5 telemetry* and the symbol's contract details against the broker's specification. The portal may display USD cents with a different currency label from MT5.

## Configure exact local scope

Set these values in the deployment's untracked `.env` file, not in Git. A fresh authenticated MT5 heartbeat confirmed symbol `XAUUSD_l` and account currency `USD` for this cent account. Match the currency and symbol against fresh telemetry on each activation; the broker portal's display is not the MT5 currency code.

```dotenv
RAMON_REAL_CENT_INSTANCE=ramon-command-ea-1
RAMON_REAL_CENT_LOGIN=<verified MT5 login>
RAMON_REAL_CENT_SERVER=<exact MT5 server>
RAMON_REAL_CENT_CURRENCY=USD
RAMON_REAL_CENT_SYMBOL=XAUUSD_l
RAMON_EA_COMMAND_CREATION_ENABLED=0
RAMON_EA_COMMAND_EXECUTION_ENABLED=0
RAMON_REAL_CENT_PROFIT_PROTECTION_ENABLED=0
RAMON_SCALP_EXECUTION_ENABLED=0
```

All five scope fields are required. Django refuses decisions, order creation and delivery on any account with a different instance, login, server, currency, symbol or trade mode. Workers use the same scope. Keep separate strategy/scalper/profit protection scope fields bound to the same instance/login/server if those features are enabled. Profit protection and scalper execution have their own disabled-by-default flags. The legacy EA is not installed by the update script; do not attach it alongside the command EA.

## Verify before allowing orders

1. Run `docker compose config` and `docker compose up -d --build --wait` without deleting named volumes.
2. Confirm the MT5 command EA v1.09 is attached once to `XAUUSD_l` and its fresh heartbeat reports mode `2`, the exact login/server/currency, `TELEMETRY_OK`, enabled terminal trading and positive broker tick/volume metadata. Confirm balance and tick value are in the same account units. Cent conversion must never be applied a second time to risk sizing.
3. Inspect `/api/ea/v1/dashboard`: the execution boundary must show the configured account scope, fresh heartbeat and symbol match. Review per-trade sizing at the broker's minimum volume and confirm it does not exceed the 0.5% server cap. If minimum volume exceeds the budget, the trade must be rejected.
4. Review any existing pending, claimed or reconciliation-required commands from the previous demo account. Let them expire or reconcile them; do not replay them on the real account.
5. Enable `RAMON_EA_COMMAND_CREATION_ENABLED=1` and `RAMON_EA_COMMAND_EXECUTION_ENABLED=1` in the local environment only after checking the above. Recreate Django and the strategy workers so each reads the same scope; confirm a genuine approved decision produces one account-scoped order. Do not force a signal or trade to test this path.
6. Confirm the broker retcode, acknowledgement, new position telemetry and account-denominated realized P/L. Partial or ambiguous results require reconciliation before further action. Passing CI and a healthy API do not prove broker execution.

To disarm command execution, set both command flags to `0` and recreate the affected containers. Do not remove broker-side stops from existing positions while disarming new entries.
