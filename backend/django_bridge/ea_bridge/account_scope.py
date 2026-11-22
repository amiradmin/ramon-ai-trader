"""Explicit broker identity required for the real cent account.

MT5 has no cent trade mode. The operator must verify the account currency and
contract metadata against the broker before setting this local scope.
"""
from django.conf import settings


def real_cent_scope(heartbeat):
    """Return (allowed, reason); never infer cent status from trade mode alone."""
    expected = tuple(str(getattr(settings, key, "")).strip() for key in (
        "RAMON_REAL_CENT_INSTANCE", "RAMON_REAL_CENT_LOGIN",
        "RAMON_REAL_CENT_SERVER", "RAMON_REAL_CENT_CURRENCY",
        "RAMON_REAL_CENT_SYMBOL",
    ))
    if not all(expected):
        return False, "REAL_CENT_SCOPE_NOT_CONFIGURED"
    if heartbeat is None:
        return False, "HEARTBEAT_MISSING"
    if (heartbeat.instance_id, heartbeat.account_login, heartbeat.server_name, heartbeat.symbol) != (
        expected[0], expected[1], expected[2], expected[4]
    ):
        return False, "REAL_CENT_SCOPE_MISMATCH"
    payload = heartbeat.payload if isinstance(heartbeat.payload, dict) else {}
    if type(payload.get("account_trade_mode")) is not int or payload["account_trade_mode"] != 2:
        return False, "REAL_ACCOUNT_REQUIRED"
    if payload.get("server_name") != expected[2]:
        return False, "ACCOUNT_SERVER_MISMATCH"
    account = payload.get("account")
    if not isinstance(account, dict) or account.get("currency") != expected[3]:
        return False, "REAL_CENT_CURRENCY_MISMATCH"
    return True, ""
