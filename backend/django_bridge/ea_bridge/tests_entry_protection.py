from django.test import SimpleTestCase
from .entry_protection import entry_report

class EntryProtectionTests(SimpleTestCase):
    def setUp(self):
        self.payload=dict(side="BUY",stop_loss=99,take_profit=103,reserved_risk_money=10)
        self.market=dict(bid=99.9,ask=100,point=.01)
        self.obs=dict(version=1,mode="OBSERVATION_ONLY",bid=99.9,ask=100,point=.01,
                      tick_size=.01,stops_level_points=10,quote_age_ms=500,
                      stop_pnl_money=-10,target_pnl_money=30)

    def run_report(self):
        return entry_report(self.payload,self.market,{"entry_observation":self.obs})

    def test_clear(self):
        r=self.run_report()
        self.assertEqual(r["status"],"CLEAR")
        self.assertEqual(r["reward_risk"],3)

    def test_unavailable(self):
        self.assertEqual(entry_report(self.payload,self.market,{})["status"],"UNAVAILABLE")

    def test_missing_not_clear(self):
        del self.obs["stop_pnl_money"]
        self.assertEqual(self.run_report()["status"],"INCOMPLETE")

    def test_buy_stop_checked_against_bid(self):
        self.payload["stop_loss"]=99.95
        self.assertIn("INVALID_STOP_OR_TARGET_DIRECTION",self.run_report()["warnings"])

    def test_sell_stop_checked_against_ask(self):
        self.payload.update(side="SELL",stop_loss=99.95,take_profit=98)
        self.assertIn("INVALID_STOP_OR_TARGET_DIRECTION",self.run_report()["warnings"])

    def test_sell_favorable_move(self):
        self.payload.update(side="SELL",stop_loss=102,take_profit=98)
        self.obs.update(bid=100.2,ask=100.3)
        self.assertLess(self.run_report()["adverse_entry_points"],0)

    def test_distance_and_grid(self):
        self.payload["stop_loss"]=99.855
        r=self.run_report()
        self.assertIn("BELOW_BROKER_STOP_DISTANCE",r["warnings"])
        self.assertIn("OFF_TICK_GRID",r["warnings"])

    def test_risk_and_reward(self):
        self.obs.update(stop_pnl_money=-15,target_pnl_money=10)
        r=self.run_report()
        self.assertIn("ESTIMATED_LOSS_ABOVE_RESERVED_RISK",r["warnings"])
        self.assertIn("REWARD_RISK_BELOW_DIAGNOSTIC_1_2",r["warnings"])

    def test_adverse_spread_and_age(self):
        self.obs.update(bid=100.1,ask=100.4,quote_age_ms=6000)
        r=self.run_report()
        for code in ("ADVERSE_ENTRY_OVER_REFERENCE_SPREAD","SPREAD_OVER_1_5_REFERENCE","QUOTE_AGE_OUTSIDE_DIAGNOSTIC_RANGE"):
            self.assertIn(code,r["warnings"])

    def test_missing_reference(self):
        self.market={}
        self.assertEqual(self.run_report()["status"],"INCOMPLETE")
