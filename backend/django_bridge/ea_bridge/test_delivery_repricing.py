"""Deterministic research tests; no live terminal or broker required."""
from decimal import Decimal
from django.test import SimpleTestCase
from .delivery_repricing import DeliveryRiskError, reprice_strategy_open


class DeliveryRepricingTests(SimpleTestCase):
    def setUp(self):
        self.plan = dict(side="SELL", volume=0.02, stop_loss=3867.21479,
                         take_profit=3834.0840125, reserved_risk_money=29.44958)
        self.quote = dict(bid="3852.43", ask="3852.65", point="0.01")
        self.spec = dict(tick_size="0.01", tick_value="1.0",
                         volume_min="0.01", volume_max="100",
                         volume_step="0.01", stops_level_points=0)

    def call(self, plan=None, quote=None, spec=None, budget="29.44958", balance="10000"):
        return reprice_strategy_open(
            self.plan if plan is None else plan,
            self.quote if quote is None else quote,
            self.spec if spec is None else spec,
            approved_risk_money=budget, account_balance=balance)

    def test_reduces_volume_instead_of_overrisk(self):
        adjusted, info = self.call()
        self.assertEqual(Decimal(str(adjusted["volume"])), Decimal("0.01"))
        self.assertTrue(info["volume_reduced"])
        self.assertEqual(adjusted["stop_loss"], self.plan["stop_loss"])
        self.assertEqual(adjusted["take_profit"], self.plan["take_profit"])

    def test_minimum_lot_rejects_when_market_moves(self):
        plan = dict(self.plan, volume=0.01, stop_loss=3752.89849,
                    take_profit=3710.6918875)
        quote = dict(self.quote, bid="3734.03", ask="3734.25")
        with self.assertRaisesRegex(DeliveryRiskError, "MINIMUM_LOT_EXCEEDS_RISK"):
            self.call(plan=plan, quote=quote, budget="18.75849")

    def test_original_volume_never_increases(self):
        plan = dict(self.plan, volume=0.01)
        adjusted, info = self.call(plan=plan, budget="200")
        self.assertEqual(adjusted["volume"], 0.01)
        self.assertFalse(info["volume_reduced"])

    def test_rejects_reward_risk_deterioration(self):
        quote = dict(self.quote, bid="3840", ask="3840.2")
        with self.assertRaisesRegex(DeliveryRiskError, "REWARD_RISK_TOO_LOW"):
            self.call(quote=quote, budget="100")

    def test_rejects_broker_stop_distance(self):
        spec = dict(self.spec, stops_level_points=2000)
        with self.assertRaisesRegex(DeliveryRiskError, "BROKER_STOP_DISTANCE"):
            self.call(spec=spec)

    def test_rejects_invalid_contract_metadata(self):
        spec = dict(self.spec, tick_value=0)
        with self.assertRaises(DeliveryRiskError):
            self.call(spec=spec)

    def test_rejects_off_step_original_lot(self):
        plan = dict(self.plan, volume=0.015)
        with self.assertRaisesRegex(DeliveryRiskError, "INVALID_ORIGINAL_VOLUME"):
            self.call(plan=plan)

    def test_balance_risk_ceiling_is_applied(self):
        adjusted, diag = self.call(budget="100", balance="2000")
        self.assertEqual(adjusted["volume"], 0.01)
        self.assertLessEqual(Decimal(diag["delivery_risk_money"]), Decimal("10"))

    def test_buy_uses_ask_and_preserves_stops(self):
        plan = dict(side="BUY", volume=0.03, stop_loss=98, take_profit=110)
        quote = dict(bid=100, ask=100.2, point=0.01)
        adjusted, diag = self.call(plan=plan, quote=quote, budget="15")
        self.assertEqual(adjusted["stop_loss"], 98)
        self.assertEqual(adjusted["take_profit"], 110)
        self.assertLessEqual(Decimal(diag["delivery_risk_money"]), Decimal("15"))
