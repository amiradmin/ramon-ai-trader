from types import SimpleNamespace
from datetime import datetime, timezone
from django.test import SimpleTestCase
from .real_performance import attribution, metrics

class RealPerformanceTests(SimpleTestCase):
    def setUp(self):
        self.outcome=SimpleNamespace(tags={"command_id":"c1","broker_ticket":"123"},
            instance_id="ea",account_login="a",symbol="XAUUSD_l",decision_id="d1",
            closed_at=datetime(2026,11,25,tzinfo=timezone.utc))
        self.command=SimpleNamespace(action="OPEN",state="EXECUTED",execution_authorized=True,
            instance_id="ea",account_login="a",symbol="XAUUSD_l",decision_id="d1",
            command_id="c1",result={"broker_ticket":"123"},completed_at=None,
            payload={"server_name":"live","engine":"STRATEGY","strategy_setup":"TREND_PULLBACK","magic":26092301})

    def test_verified_strategy(self):
        self.assertEqual(attribution(self.outcome,self.command,"live"),("TREND_PULLBACK","VERIFIED_COMMAND"))

    def test_verified_scalper(self):
        self.command.payload.update(engine="SCALPER",magic=26092213)
        self.assertEqual(attribution(self.outcome,self.command,"live")[0],"FAST_SCALP")

    def test_wrong_server(self):
        self.assertEqual(attribution(self.outcome,self.command,"other")[0],"UNATTRIBUTED")

    def test_wrong_ticket(self):
        self.outcome.tags["broker_ticket"]="999"
        self.assertEqual(attribution(self.outcome,self.command,"live")[0],"UNATTRIBUTED")

    def test_unauthorized(self):
        self.command.execution_authorized=False
        self.assertEqual(attribution(self.outcome,self.command,"live")[0],"UNATTRIBUTED")

    def test_missing_command(self):
        self.assertEqual(attribution(self.outcome,None,"live")[0],"UNATTRIBUTED")

    def test_wrong_magic(self):
        self.command.payload["magic"]=1
        self.assertEqual(attribution(self.outcome,self.command,"live")[0],"UNATTRIBUTED")

    def test_metrics(self):
        rows=[dict(net_pnl=str(p),closed_at=str(i),execution_metrics={"commission":-1})
              for i,p in enumerate([10,-4,-3,2])]
        m=metrics(rows)
        self.assertEqual(m["net_pnl"],5)
        self.assertEqual(m["closed_pnl_drawdown"],7)
        self.assertEqual(m["win_rate"],50)
        self.assertEqual(m["longest_losing_timestamp_streak"],2)
        self.assertEqual(m["costs"]["commission"]["reported_sum"],-4)
        self.assertIsNone(m["costs"]["swap"]["reported_sum"])

    def test_empty_metrics(self):
        m=metrics([])
        self.assertEqual(m["outcomes"],0)
        self.assertIsNone(m["profit_factor"])
        self.assertIsNone(m["win_rate"])
