from unittest.mock import patch
from django.test import SimpleTestCase
from .position_observer import hypotheses, build_report

class PositionObserverTests(SimpleTestCase):
    def buy(self, bid=102, stop=99, target=105, atr=.5, distance=.1):
        return hypotheses("BUY",100,99,stop,target,bid,bid+.1,.01,distance,atr)

    def test_initial_risk(self):
        self.assertEqual(hypotheses("BUY",100,101,99,105,102,102.1,.01,.1)["status"],"INVALID_INITIAL_RISK")

    def test_buy_breakeven(self):
        r=self.buy()
        self.assertEqual(r["current_r"],2)
        self.assertEqual(r["hypotheses"][1]["status"],"HYPOTHETICAL_TIGHTEN")
        self.assertEqual(r["hypotheses"][1]["proposed_stop"],100)

    def test_sell_uses_ask(self):
        r=hypotheses("SELL",100,101,101,95,97.8,98,.01,.1,.5)
        self.assertEqual(r["current_r"],2)
        self.assertEqual(r["hypotheses"][1]["status"],"HYPOTHETICAL_TIGHTEN")
        self.assertAlmostEqual(r["hypotheses"][3]["proposed_stop"],98.5)

    def test_no_widening(self):
        r=self.buy(stop=101.9)
        self.assertTrue(all(x["status"]=="ALREADY_PROTECTED" for x in r["hypotheses"]))

    def test_trigger(self):
        r=self.buy(bid=100.6)
        self.assertEqual(r["hypotheses"][0]["status"],"HYPOTHETICAL_TIGHTEN")
        self.assertEqual(r["hypotheses"][1]["status"],"WAIT")
        self.assertEqual(r["hypotheses"][3]["status"],"WAIT")

    def test_broker_distance(self):
        self.assertEqual(self.buy(distance=3)["hypotheses"][0]["status"],"BROKER_DISTANCE")

    def test_missing_atr(self):
        self.assertEqual(len(self.buy(atr=None)["hypotheses"]),3)

    def test_remaining_rr(self):
        self.assertAlmostEqual(self.buy()["remaining_reward_risk"],1)

    @patch("ea_bridge.position_observer.EAHeartbeat.objects.filter")
    def test_no_feed(self, query):
        query.return_value.first.return_value=None
        r=build_report()
        self.assertFalse(r["affects_orders"])
        self.assertEqual(r["positions"],[])
        self.assertNotEqual(r["status"],"READY")
