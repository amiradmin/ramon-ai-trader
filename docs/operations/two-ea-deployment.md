# Deploying the Ramon EAs together

## Current limitation

The `mt5/` directory in this repository contains the command-executor EA variants, including `RamonCommandEA_v1.02.mq5`. It does not contain the separate `Ramon_v0.62.0.mq5` source, so this repository alone cannot verify compatibility between the two programs.

## Intended responsibilities

- Django remains the single authority for strategy decisions and risk approval.
- `RamonCommandEA_v1.02` reports telemetry and executes commands returned by Django. Keep its `EnableCommandExecution` input set to `false` during setup and verification.
- Do not assume `Ramon_v0.62.0` is safe to run alongside the command EA until its exact source is reviewed. In particular, confirm whether it submits orders itself, calls another decision endpoint, or sends telemetry that could conflict with the command EA.
- Never run two command executors that could execute the same command.

## Verification sequence

1. Install and compile the exact `RamonCommandEA_v1.02.mq5` source from this repository in the active MT5 terminal.
2. Confirm fresh Django telemetry reports `ea_version="1.02"` and integer `account_trade_mode=0` for the intended demo account.
3. Keep command creation/execution flags disabled while verifying telemetry and decision/risk output.
4. Confirm a broker-verified daily opening-balance baseline exists for the exact account, server, and broker-local date. Do not guess the baseline.
5. Review the exact `Ramon_v0.62.0.mq5` source and verify it cannot independently submit orders or conflict with Django's authoritative decisions before attaching it.
6. Only proceed with demo execution after these checks pass. Orders must result from genuine Django decisions that pass all risk checks; never force a trade or bypass a guard.

Keep execution disabled if EA version/account mode is missing, the baseline is absent, the installed binary cannot be tied to the reviewed source, or the two EAs conflict. Do not delete database volumes to troubleshoot this setup.
