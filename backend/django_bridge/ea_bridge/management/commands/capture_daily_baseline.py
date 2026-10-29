"""Explicit, auditable bootstrap for a broker-verified daily opening balance."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from ea_bridge.models import AccountDailyBaseline


class Command(BaseCommand):
    help = (
        "Record a broker-verified daily opening balance. This never derives "
        "the opening balance from the current account balance and never "
        "overwrites an existing baseline."
    )

    def add_arguments(self, parser):
        parser.add_argument("--account-login", required=True)
        parser.add_argument("--server-name", required=True)
        parser.add_argument("--broker-day", required=True, help="Broker date in YYYY-MM-DD format")
        parser.add_argument("--opening-balance", required=True, help="Opening balance verified against broker history")
        parser.add_argument("--source-instance-id", required=True, help="EA instance used to identify the account")
        parser.add_argument(
            "--confirm-broker-opening-balance",
            action="store_true",
            help="Required acknowledgement that the value was verified from broker records",
        )

    def handle(self, *args, **options):
        account_login = options["account_login"].strip()
        server_name = options["server_name"].strip()
        source_instance_id = options["source_instance_id"].strip()
        if not account_login or len(account_login) > 32:
            raise CommandError("--account-login must contain 1-32 characters")
        if not server_name or len(server_name) > 128:
            raise CommandError("--server-name must contain 1-128 characters")
        if not source_instance_id or len(source_instance_id) > 128:
            raise CommandError("--source-instance-id must contain 1-128 characters")
        if not options["confirm_broker_opening_balance"]:
            raise CommandError(
                "Refusing to create a baseline without --confirm-broker-opening-balance. "
                "Verify the opening balance from broker records; do not use the current balance."
            )
        try:
            broker_day = date.fromisoformat(options["broker_day"])
        except (TypeError, ValueError):
            raise CommandError("--broker-day must be a valid YYYY-MM-DD date") from None
        if broker_day.isoformat() != options["broker_day"]:
            raise CommandError("--broker-day must be a valid YYYY-MM-DD date")
        if broker_day > date.today():
            raise CommandError("--broker-day cannot be in the future")
        try:
            opening_balance = Decimal(options["opening_balance"])
        except (InvalidOperation, TypeError, ValueError):
            raise CommandError("--opening-balance must be a positive decimal amount") from None
        if not opening_balance.is_finite() or opening_balance <= 0:
            raise CommandError("--opening-balance must be a positive finite decimal amount")
        # AccountDailyBaseline.opening_balance has max_digits=24 and decimal_places=2.
        if opening_balance.adjusted() > 21:
            raise CommandError("--opening-balance exceeds the supported precision")
        try:
            opening_balance = opening_balance.quantize(Decimal("0.01"))
        except InvalidOperation:
            raise CommandError("--opening-balance exceeds the supported precision") from None

        lookup = {
            "account_login": account_login,
            "server_name": server_name,
            "broker_day": broker_day,
        }
        try:
            with transaction.atomic():
                _, created = AccountDailyBaseline.objects.get_or_create(
                    **lookup,
                    defaults={
                        "opening_balance": opening_balance,
                        "source_instance_id": source_instance_id,
                    },
                )
        except IntegrityError as exc:
            raise CommandError("Could not create baseline due to a concurrent write; inspect the existing record.") from exc

        if not created:
            raise CommandError(
                "A baseline already exists for this account, server, and broker day. "
                "Baselines are immutable; inspect the existing record instead of overwriting it."
            )

        self.stdout.write(self.style.SUCCESS(
            f"Created broker-verified baseline for account={account_login}, "
            f"server={server_name}, day={broker_day}, opening_balance={opening_balance:.2f}, "
            f"source_instance_id={source_instance_id}. Trading authorization remains unchanged."
        ))
