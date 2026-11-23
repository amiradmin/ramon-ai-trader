"""Historical replay endpoints stay read-only and use a single candle source."""
from datetime import datetime, timedelta, timezone
from django.test import TestCase
from .models import MarketBar, LearningTradeOutcome

class HistoricalReplayTests(TestCase):
    def setUp(self):
        stamp = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
        for index in range(3):
            MarketBar.objects.create(
                source="broker-a", symbol="XAUUSD_l", timeframe="M5",
                timestamp=stamp + timedelta(minutes=5 * index),
                open="2600", high="2602", low="2599", close=str(2600 + index), volume="12",
            )
        MarketBar.objects.create(
            source="broker-b", symbol="XAUUSD_l", timeframe="M5",
            timestamp=stamp + timedelta(minutes=10),
            open="2700", high="2702", low="2699", close="2701", volume="5",
        )

    def test_page_is_accessible(self):
        response = self.client.get("/api/ea/v1/simulator")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Step Forward")
        self.assertContains(response, 'id="testStrategies"')
        choices=response.content.split(b'id="testStrategies"',1)[1].split(b'</div>',1)[0]
        self.assertNotIn(b' checked',choices)
        self.assertNotContains(response,'id="compareBalances" type="checkbox" checked')
        self.assertContains(response, 'id="testOpportunityScore"')
        self.assertContains(response, 'id="testEntryQuality"')
        self.assertEqual(response.content.count(b'type="checkbox" value="'), 8)

    def test_frozen_forward_command_does_not_retest_development_history(self):
        from io import StringIO
        from django.core.management import call_command
        MarketBar.objects.create(
            source="litefinance-mt5-live-export-20261122", symbol="XAUUSD_l",
            timeframe="M15", timestamp=datetime(2026, 11, 20, 12, tzinfo=timezone.utc),
            open="3500", high="3501", low="3499", close="3500", volume="1",
        )
        output=StringIO()
        call_command("evaluate_frozen_forward", stdout=output)
        self.assertIn("WAITING: no new complete-day M15 candles after 2026-11-22", output.getvalue())

    def test_validated_bars_are_ordered_and_single_source(self):
        response = self.client.get("/api/ea/v1/simulator/bars", {
            "symbol": "XAUUSD_l", "timeframe": "M5", "source": "broker-a",
        })
        self.assertEqual(response.status_code, 200)
        rows = response.json()["bars"]
        self.assertEqual([item["close"] for item in rows], [2600., 2601., 2602.])
        self.assertEqual(len(rows), 3)

    def test_before_limit_is_inclusive(self):
        response = self.client.get("/api/ea/v1/simulator/bars", {
            "symbol": "XAUUSD_l", "timeframe": "M5", "source": "broker-a",
            "before": "2026-01-02T12:05:00Z",
        })
        self.assertEqual(len(response.json()["bars"]), 2)

    def test_large_date_range_loads_first_window_without_claiming_full_coverage(self):
        response = self.client.get("/api/ea/v1/simulator/bars", {
            "symbol": "XAUUSD_l", "timeframe": "M5", "source": "broker-a",
            "start": "2026-01-02T12:00:00Z",
            "before": "2026-01-03T00:00:00Z", "limit": 2,
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["truncated"])
        self.assertEqual(body["window"], "earliest_in_range")
        self.assertEqual([b["close"] for b in body["bars"]], [2600., 2601.])

    def test_next_page_uses_exclusive_cursor_and_does_not_repeat_bar(self):
        first=self.client.get("/api/ea/v1/simulator/bars",{
            "symbol":"XAUUSD_l","timeframe":"M5","source":"broker-a",
            "start":"2026-01-02T12:00:00Z","before":"2026-01-03T00:00:00Z",
            "limit":2,
        }).json()
        self.assertTrue(first["has_next"])
        self.assertEqual(len(first["bars"]),2)
        cursor=datetime.fromtimestamp(first["bars"][-1]["time"], tz=timezone.utc).isoformat()
        second=self.client.get("/api/ea/v1/simulator/bars",{
            "symbol":"XAUUSD_l","timeframe":"M5","source":"broker-a",
            "start":"2026-01-02T12:00:00Z","before":"2026-01-03T00:00:00Z",
            "after":cursor,"limit":2,
        }).json()
        self.assertEqual([bar["close"] for bar in second["bars"]],[2602.0])
        self.assertFalse(second["has_next"])
        self.assertNotIn(first["bars"][-1]["time"],[bar["time"] for bar in second["bars"]])

    def test_after_rejects_invalid_timestamp_or_unbounded_query(self):
        base={"symbol":"XAUUSD_l","timeframe":"M5"}
        for extra in ({"after":"not-a-date"},
                      {"start":"2026-01-02T12:00:00Z","after":"bad"}):
            response=self.client.get("/api/ea/v1/simulator/bars",{**base,**extra})
            self.assertEqual(response.status_code,400)

    def test_invalid_queries_fail_closed(self):
        for params in [
            {"symbol": "XAUUSD_l", "timeframe": "INVALID"},
            {"symbol": "XAUUSD_l", "limit": "999999"},
            {"symbol": "XAUUSD_l", "before": "yesterday"},
            {"timeframe": "M5"},
        ]:
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/api/ea/v1/simulator/bars", params).status_code, 400)

    def test_unknown_symbol_returns_empty_history(self):
        response = self.client.get("/api/ea/v1/simulator/bars", {"symbol": "UNKNOWN"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["bars"], [])

class ReplayRecordedPerformanceTests(TestCase):
    def setUp(self):
        for trade_id, minute, pnl, account in [
            ("trade-a", 5, "12.5", "one"),
            ("trade-b", 10, "-4.0", "one"),
            ("trade-c", 15, "100.0", "one"),
        ]:
            LearningTradeOutcome.objects.create(
                trade_id=trade_id, symbol="XAUUSD_o", account_login=account,
                realized_pnl=pnl,
                closed_at=datetime(2026, 1, 2, 12, minute, tzinfo=timezone.utc),
            )

    def stats(self, through, start="2026-01-02T12:00:00Z"):
        return self.client.get("/api/ea/v1/simulator/recorded-stats", {
            "symbol": "XAUUSD_o", "start": start, "through": through,
        })

    def test_cursor_only_counts_closed_trades_up_to_position(self):
        first = self.stats("2026-01-02T12:05:00Z").json()
        self.assertEqual(first["trade_count"], 1)
        self.assertEqual(first["win_rate_percent"], 100.0)
        self.assertEqual(first["total_gain"], "12.50000000")
        second = self.stats("2026-01-02T12:10:00Z").json()
        self.assertEqual(second["trade_count"], 2)
        self.assertEqual(second["win_rate_percent"], 50.0)
        self.assertEqual(second["total_gain"], "8.50000000")

    def test_no_trade_is_not_zero_win_rate(self):
        result = self.stats("2026-01-02T12:04:00Z").json()
        self.assertEqual(result["trade_count"], 0)
        self.assertIsNone(result["win_rate_percent"])
        self.assertIsNone(result["total_gain"])

    def test_mixed_accounts_hide_net_gain(self):
        LearningTradeOutcome.objects.create(
            trade_id="other-account", symbol="XAUUSD_o",
            account_login="two", realized_pnl="20",
            closed_at=datetime(2026, 1, 2, 12, 8, tzinfo=timezone.utc),
        )
        result = self.stats("2026-01-02T12:10:00Z").json()
        self.assertTrue(result["mixed_accounts"])
        self.assertIsNone(result["total_gain"])

    def test_invalid_dates_are_rejected(self):
        self.assertEqual(self.stats("not-a-date").status_code, 400)
        self.assertEqual(self.stats("2026-01-02T11:00:00Z").status_code, 400)

class HistoricalStrategyBacktestTests(TestCase):
    def setUp(self):
        self.path="/api/ea/v1/simulator/backtest"
        self.params=dict(symbol="XAUUSD_o",source="historical-test",
            start="2026-01-01",end="2026-01-04",
            balance="10000",spread_points="20",point="0.01",
            tick_size="0.01",tick_value="1",volume_min="0.01",
            volume_max="10",volume_step="0.01")
        base=datetime(2025,12,30,0,0,tzinfo=timezone.utc)
        for i in range(240):
            stamp=base+timedelta(minutes=15*i)
            MarketBar.objects.create(source="historical-test",symbol="XAUUSD_o",timeframe="M15",
                timestamp=stamp,open="2600",high="2602",low="2598",close="2600",volume="1")

    def test_historical_candle_quality_never_resamples_or_invents_prices(self):
        from .replay_backtest import _clean_m15_rows
        rows=[
            {"time":900,"close":1},
            {"time":901,"close":99},
            {"time":1800,"close":2},
            {"time":1800,"close":88},
            {"time":3600,"close":3},
        ]
        accepted, quality=_clean_m15_rows(rows)
        self.assertEqual([row["close"] for row in accepted],[1,2,3])
        self.assertEqual(quality["excluded"]["off_grid"],1)
        self.assertEqual(quality["excluded"]["duplicate_slot"],1)
        self.assertEqual(quality["gap_count"],1)

    def test_every_strategy_has_explicit_research_setup_only(self):
        from .replay_backtest import _research_pipeline
        from .strategy_registry import STRATEGIES
        for setup in STRATEGIES:
            with self.subTest(setup=setup):
                pipeline=_research_pipeline(setup)
                configs=pipeline.opportunities.trading_settings["opportunity_finder"]["setups"]
                self.assertEqual(
                    [name for name,policy in configs.items() if policy["enabled"]],
                    [setup.lower()],
                )
                self.assertLessEqual(pipeline.risk_manager.maximum_risk_percent,0.5)

    def test_validated_replay_matches_exact_backtest_filter(self):
        from .replay_backtest import _clean_m15_rows
        qs=MarketBar.objects.filter(source="historical-test",
            symbol="XAUUSD_o",timeframe="M15").order_by("timestamp")
        source=[{"time":int(row.timestamp.timestamp()),"open":float(row.open),
            "high":float(row.high),"low":float(row.low),"close":float(row.close)}
            for row in qs]
        cleaned,_quality=_clean_m15_rows(source)
        page=self.client.get("/api/ea/v1/simulator/bars",{
            "symbol":"XAUUSD_o","source":"historical-test","timeframe":"M15",
            "validated":"1","start":"2025-12-30T00:00:00Z",
            "before":"2026-01-05T00:00:00Z","limit":300,
        })
        self.assertEqual(page.status_code,200,page.content)
        self.assertTrue(page.json()["validated"])
        self.assertEqual([r["time"] for r in page.json()["bars"]],
            [r["time"] for r in cleaned])

    def test_validated_chart_drops_offgrid_rows_without_rewriting_source(self):
        stamp=datetime(2026,1,1,12,0,tzinfo=timezone.utc)
        MarketBar.objects.create(source="historical-test",symbol="XAUUSD_o",
            timeframe="M15",timestamp=stamp+timedelta(seconds=1),
            open="9999",high="9999",low="9999",close="9999",volume="1")
        response=self.client.get("/api/ea/v1/simulator/bars",{
            "symbol":"XAUUSD_o","source":"historical-test","timeframe":"M15",
            "validated":"1","start":"2026-01-01T00:00:00Z",
            "before":"2026-01-05T00:00:00Z","limit":300})
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(any(row["close"]==9999 for row in response.json()["bars"]))
        self.assertTrue(MarketBar.objects.filter(
            source="historical-test",timestamp=stamp+timedelta(seconds=1)).exists())

    def test_requires_explicit_symbol_economics(self):
        self.params.pop("tick_value")
        response=self.client.get(self.path,self.params)
        self.assertEqual(response.status_code,400)
        self.assertIn("tick value",response.json()["error"])

    def test_rejects_reverse_dates(self):
        self.params["start"]="2026-01-08"
        response=self.client.get(self.path,self.params)
        self.assertEqual(response.status_code,400)

    def test_rejections_reflect_actual_pipeline_stage_reasons(self):
        from unittest.mock import patch
        class Pipeline:
            class Risk:
                class Config:
                    target_reward_risk=1.5
                config=Config()
            risk_manager=Risk()
            def run(self,market):
                return {"approved":False,"action":"HOLD","rejected_by":"risk_management",
                        "stages":{"risk_management":{"approved":False,
                            "reason":"RISK_BUDGET_BELOW_MINIMUM_VOLUME"}}}
        with patch("ea_bridge.replay_backtest._research_pipeline",return_value=Pipeline()):
            response=self.client.get(self.path,self.params)
        self.assertEqual(response.status_code,200,response.content)
        for strategy in response.json()["strategies"]:
            self.assertGreater(strategy["evaluations"],0)
            self.assertGreater(strategy["rejection_total"],0)
            reasons={(row["stage"],row["reason"]) for row in strategy["rejections"]}
            self.assertIn(("RISK_MANAGEMENT","RISK_BUDGET_BELOW_MINIMUM_VOLUME"),reasons)

    def test_selected_strategies_only_are_evaluated_and_reported(self):
        from unittest.mock import patch
        from .strategy_registry import STRATEGIES
        selected=["TREND_PULLBACK","RANGE_EDGE_FADE"]
        class Pipeline:
            class Risk:
                class Config:
                    target_reward_risk=1.5
                config=Config()
            risk_manager=Risk()
            def run(self,market):
                return {"approved":False,"action":"HOLD"}
        with patch("ea_bridge.replay_backtest._research_pipeline",return_value=Pipeline()) as factory:
            response=self.client.get(self.path,{**self.params,"setups":selected})
        self.assertEqual(response.status_code,200,response.content)
        report=response.json()
        self.assertEqual(report["selected_setups"],selected)
        self.assertEqual([item["setup"] for item in report["strategies"]],selected)
        self.assertEqual(factory.call_count,2)
        self.assertTrue(set(selected).issubset(STRATEGIES))

    def test_research_tuning_changes_only_isolated_pipeline(self):
        from .replay_backtest import _research_pipeline
        baseline=_research_pipeline("TREND_PULLBACK")
        tuned=_research_pipeline("TREND_PULLBACK",risk_percent=0.2,
            stop_buffer_atr=0.5,target_reward_risk=2.0,
            minimum_opportunity_score=0.7,minimum_entry_quality=0.8)
        self.assertEqual(tuned.risk_manager.maximum_risk_percent,0.2)
        self.assertEqual(tuned.risk_manager.config.stop_buffer_atr,0.5)
        self.assertEqual(tuned.risk_manager.config.target_reward_risk,2.0)
        self.assertEqual(tuned.opportunities.minimum_score,0.7)
        self.assertEqual(tuned.entry_timer.minimum_quality,0.8)
        self.assertNotEqual(baseline.opportunities.minimum_score,0.7)
        self.assertNotEqual(baseline.entry_timer.minimum_quality,0.8)
        self.assertEqual(baseline.risk_manager.config.stop_buffer_atr,0.25)
        self.assertNotEqual(baseline.risk_manager.config.target_reward_risk,2.0)

    def test_trade_diagnostics_group_realized_outcomes_without_filtering(self):
        from .replay_backtest import _trade_diagnostics
        from .models import EACommand
        trades=[
            dict(opened_at=int(datetime(2026,10,1,2,tzinfo=timezone.utc).timestamp()),
                side="BUY",signal_regime="BULLISH",gaps_crossed=0,reason="TARGET",
                pnl=60.0,gross_pnl=61.0,commission=1.0),
            dict(opened_at=int(datetime(2026,10,2,14,tzinfo=timezone.utc).timestamp()),
                side="SELL",signal_regime="BEARISH",gaps_crossed=1,reason="STOP",
                pnl=-48.0,gross_pnl=-47.0,commission=1.0),
            dict(opened_at=int(datetime(2026,11,2,14,tzinfo=timezone.utc).timestamp()),
                side="SELL",signal_regime="BEARISH",gaps_crossed=0,reason="TARGET",
                pnl=55.0,gross_pnl=56.0,commission=1.0),
        ]
        report=_trade_diagnostics(trades)
        self.assertEqual(report["gap_affected_trades"],1)
        self.assertEqual(report["commission"],3)
        self.assertEqual(report["gross_pnl"],70)
        self.assertEqual([(r["label"],r["trades"],r["net_pnl"]) for r in report["by_month"]],
                         [("2026-10",2,12.0),("2026-11",1,55.0)])
        self.assertEqual([(r["label"],r["net_pnl"]) for r in report["by_exit"]],
                         [("STOP",-48.0),("TARGET",115.0)])
        self.assertFalse(EACommand.objects.exists())

    def test_gap_and_signal_regime_are_attached_to_simulated_trade(self):
        from unittest.mock import patch
        from .replay_backtest import _simulate
        class Pipeline:
            class Risk:
                class Config:
                    target_reward_risk=1.25
                config=Config()
            risk_manager=Risk()
            def run(self,market):
                return {"approved":True,"action":"BUY",
                    "stages":{"opportunities":{"regime":{"label":"BULLISH","adx":25.0}},
                              "risk_management":{"stop_price":99,"target_price":101,"volume":1}}}
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        rows=[{"time":int((start+timedelta(minutes=15*i)).timestamp()),
               "open":100,"high":101,"low":99,"close":100} for i in range(140)]
        rows[129]["time"]+=900
        for i in range(130,140):
            rows[i]["time"]+=900
        data={"start":start.date(),"end":start.date(),"symbol":"XAUUSD_o",
              "balance":1000,"spread_points":10,"point":0.01,"tick_size":0.01,
              "tick_value":1,"volume_min":0.01,"volume_max":10,"volume_step":0.01,
              "setups":["TREND_PULLBACK"]}
        with patch("ea_bridge.replay_backtest._research_pipeline",return_value=Pipeline()):
            report=_simulate(data,rows)[0]
        self.assertGreater(report["closed_trades"],0)
        self.assertEqual(report["trades"][0]["signal_regime"],"BULLISH")
        self.assertEqual(report["trades"][0]["signal_adx"],25.0)
        self.assertEqual(report["trades"][0]["gaps_crossed"],1)
        self.assertGreaterEqual(report["trade_diagnostics"]["gap_affected_trades"],1)

    def test_assumed_slippage_and_commission_reduce_estimated_trade_pnl(self):
        from unittest.mock import patch
        from .replay_backtest import _simulate
        class Pipeline:
            class Risk:
                class Config:
                    target_reward_risk=1.25
                config=Config()
            risk_manager=Risk()
            def run(self,market):
                return {"approved":True,"action":"BUY",
                    "stages":{"risk_management":{"stop_price":99,"target_price":101,"volume":1}}}
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        rows=[{"time":int((start+timedelta(minutes=15*i)).timestamp()),
               "open":100,"high":101,"low":99,"close":100} for i in range(140)]
        data={"start":start.date(),"end":start.date(),"symbol":"XAUUSD_o",
              "balance":1000,"spread_points":10,"point":0.01,"tick_size":0.01,
              "tick_value":1,"volume_min":0.01,"volume_max":10,"volume_step":0.01,
              "setups":["TREND_PULLBACK"],"risk_percent":0.5,
              "stop_buffer_atr":0.25,"target_reward_risk":1.25}
        with patch("ea_bridge.replay_backtest._research_pipeline",return_value=Pipeline()):
            baseline=_simulate({**data,"slippage_points":0,"commission_per_lot":0},rows)[0]
            costly=_simulate({**data,"slippage_points":2,"commission_per_lot":3},rows)[0]
        self.assertGreater(baseline["closed_trades"],0)
        self.assertEqual(baseline["closed_trades"],costly["closed_trades"])
        for plain,charged in zip(baseline["trades"],costly["trades"]):
            self.assertAlmostEqual(plain["pnl"]-charged["pnl"],7)
            self.assertEqual(charged["commission"],3)
            self.assertEqual(charged["entry_slippage_points"],2)
            self.assertEqual(charged["exit_slippage_points"],2)

    def test_invalid_research_costs_and_risk_are_rejected(self):
        for extra in ({"risk_percent":"0.6"},{"risk_percent":"0"},
                      {"target_reward_risk":"1.0"},{"stop_buffer_atr":"-0.1"},
                      {"minimum_opportunity_score":"-0.01"},{"minimum_opportunity_score":"1.01"},
                      {"minimum_entry_quality":"-0.01"},{"minimum_entry_quality":"1.01"},
                      {"slippage_points":"-1"},{"commission_per_lot":"NaN"}):
            with self.subTest(extra=extra):
                response=self.client.get(self.path,{**self.params,**extra})
                self.assertEqual(response.status_code,400,response.content)

    def test_invalid_or_empty_selected_strategy_is_rejected(self):
        for setups in ([""],["FAST_SCALP","TREND_PULLBACK"],["TREND_PULLBACK","TREND_PULLBACK"]):
            with self.subTest(setups=setups):
                response=self.client.get(self.path,{**self.params,"setups":setups})
                self.assertEqual(response.status_code,400)
                self.assertTrue(response.json()["error"])

    def test_fast_scalp_needs_its_own_m1_m5_source(self):
        response=self.client.get(self.path,{**self.params,"setups":["FAST_SCALP"]})
        self.assertEqual(response.status_code,422)
        self.assertIn("M1 and M5",response.json()["error"])

    def test_fast_scalp_uses_separate_replay_without_m15_currency_profit(self):
        from unittest.mock import patch
        base=datetime(2026,1,1,tzinfo=timezone.utc)
        MarketBar.objects.bulk_create([MarketBar(source="historical-test",symbol="XAUUSD_o",
            timeframe="M1",timestamp=base+timedelta(minutes=i),
            open="2600",high="2602",low="2598",close="2600",volume="1") for i in range(165) if i != 50])
        fake={"trade_count":0,"trades":[],"win_rate":None,"net_points":0,
              "net_r":0,"closed_cumulative_r_drawdown":0,"rejections":{}}
        with patch("ea_bridge.replay_backtest.replay_scalper",return_value=fake) as runner:
            response=self.client.get(self.path,{**self.params,"setups":["FAST_SCALP"],
                "commission_per_lot":"1","slippage_points":"1"})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()["strategies"],[])
        self.assertEqual(response.json()["scalp_research"],fake)
        self.assertEqual(response.json()["scalp_data_quality"]["M5"]["accepted_candles"],32)
        self.assertEqual(response.json()["scalp_data_quality"]["M5"]["incomplete_groups"],1)
        self.assertEqual(runner.call_args.kwargs["costs"].commission_points,1)

    def test_evaluates_all_seven_read_only_without_commands(self):
        from unittest.mock import patch
        from .strategy_registry import STRATEGIES
        class Pipeline:
            class Risk:
                class Config:
                    target_reward_risk=1.5
                config=Config()
            risk_manager=Risk()
            def run(self,market):
                return {"approved":False,"action":"HOLD"}
        with patch("ea_bridge.replay_backtest._research_pipeline",return_value=Pipeline()):
            response=self.client.get(self.path,self.params)
        self.assertEqual(response.status_code,200,response.content)
        report=response.json()
        self.assertEqual(report["mode"],"ISOLATED_STRATEGY_RESEARCH_ESTIMATE")
        self.assertEqual(len(report["strategies"]),len(STRATEGIES))
        self.assertEqual(sum(s["closed_trades"] for s in report["strategies"]),0)
        from .models import EACommand
        self.assertEqual(EACommand.objects.count(),0)

class HistoricalBacktestQueueTests(TestCase):
    def test_job_queue_is_durable_and_does_not_create_ea_commands(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from .models import EACommand
        from . import replay_jobs
        params = dict(
            symbol="XAUUSD_o", source="ramon-command-ea-1",
            start="2026-07-22", end="2026-11-22",
            balance="1000", spread_points="20", point="0.01",
            tick_size="0.01", tick_value="1",
            volume_min="0.01", volume_max="100", volume_step="0.01",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(replay_jobs, "ROOT", Path(temporary)):
                response=self.client.post("/api/ea/v1/simulator/backtest/jobs", params)
                self.assertEqual(response.status_code, 202, response.content)
                identifier=response.json()["job_id"]
                job=self.client.get("/api/ea/v1/simulator/backtest/jobs/"+identifier)
                self.assertEqual(job.status_code, 200)
                self.assertEqual(job.json()["status"], "QUEUED")
                self.assertIsNone(job.json()["result"])
                self.assertEqual(self.client.post("/api/ea/v1/simulator/backtest/jobs",params).status_code,409)
                self.assertEqual(EACommand.objects.count(),0)

    def test_paired_comparison_flag_is_saved_without_commands(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from . import replay_jobs
        from .models import EACommand
        params={
            "symbol":"XAUUSD_o","source":"research-test",
            "start":"2026-09-22","end":"2026-11-22",
            "balance":"1000","spread_points":"20","point":"0.01",
            "tick_size":"0.01","tick_value":"1","volume_min":"0.01",
            "volume_max":"100","volume_step":"0.01",
            "compare_balances":"1",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(replay_jobs,"ROOT",Path(directory)):
                response=self.client.post("/api/ea/v1/simulator/backtest/jobs",params)
                self.assertEqual(response.status_code,202,response.content)
                state=replay_jobs.read(response.json()["job_id"])
                self.assertTrue(state["compare_balances"])
                self.assertEqual(EACommand.objects.count(),0)

    def test_queued_job_keeps_the_selected_strategy_list(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from . import replay_jobs
        params={
            "symbol":"XAUUSD_o","source":"research-test",
            "start":"2026-09-22","end":"2026-11-22",
            "balance":"1000","spread_points":"20","point":"0.01",
            "tick_size":"0.01","tick_value":"1","volume_min":"0.01",
            "volume_max":"100","volume_step":"0.01",
            "setups":["TREND_PULLBACK","RANGE_EDGE_FADE"],
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(replay_jobs,"ROOT",Path(directory)):
                response=self.client.post("/api/ea/v1/simulator/backtest/jobs",params)
                self.assertEqual(response.status_code,202,response.content)
                state=replay_jobs.read(response.json()["job_id"])
                self.assertEqual(state["params"]["setups"],params["setups"])

    def test_queued_job_preserves_research_tuning(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from . import replay_jobs
        params={
            "symbol":"XAUUSD_o","source":"research-test",
            "start":"2026-09-22","end":"2026-11-22",
            "balance":"1000","spread_points":"20","point":"0.01",
            "tick_size":"0.01","tick_value":"1","volume_min":"0.01",
            "volume_max":"100","volume_step":"0.01",
            "risk_percent":"0.2","stop_buffer_atr":"0.5","target_reward_risk":"2",
            "minimum_opportunity_score":"0.7","minimum_entry_quality":"0.8",
            "slippage_points":"2","commission_per_lot":"3",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(replay_jobs,"ROOT",Path(directory)):
                response=self.client.post("/api/ea/v1/simulator/backtest/jobs",params)
                self.assertEqual(response.status_code,202,response.content)
                saved=replay_jobs.read(response.json()["job_id"])["params"]
                self.assertEqual({key:saved[key] for key in (
                    "risk_percent","stop_buffer_atr","target_reward_risk",
                    "minimum_opportunity_score","minimum_entry_quality",
                    "slippage_points","commission_per_lot")},
                    {"risk_percent":0.2,"stop_buffer_atr":0.5,"target_reward_risk":2.0,
                     "minimum_opportunity_score":0.7,"minimum_entry_quality":0.8,
                     "slippage_points":2.0,"commission_per_lot":3.0})

    def test_job_api_rejects_invalid_dates(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from . import replay_jobs
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(replay_jobs,"ROOT",Path(temporary)):
                response=self.client.post("/api/ea/v1/simulator/backtest/jobs",{
                    "symbol":"XAUUSD_o", "source":"historical-test",
                    "start":"bad", "end":"2026-11-22",
                })
                self.assertEqual(response.status_code,400)
                self.assertEqual(self.client.get("/api/ea/v1/simulator/backtest/jobs/not-a-uuid").status_code,404)

class DukascopyImportTests(TestCase):
    def test_decodes_real_binary_format_and_aggregates_full_m15_only(self):
        import lzma
        import struct
        from datetime import date
        from .management.commands.import_dukascopy_xauusd import decode_day, aggregate_m15
        # Official-style BID M1 record format: time, OPEN, CLOSE, LOW, HIGH, volume.
        raw=b"".join(struct.pack(">IIIIIf",minute*60,
            3400000+minute*1000,3400500+minute*1000,3399500+minute*1000,
            3401000+minute*1000,2.0) for minute in range(16))
        rows=decode_day(lzma.compress(raw,format=lzma.FORMAT_ALONE),date(2026,10,27))
        self.assertEqual(len(rows),16)
        bars, incomplete=aggregate_m15(rows)
        self.assertEqual(len(bars),1)
        self.assertEqual(incomplete,1)
        self.assertEqual(float(bars[0][1]),3400)
        self.assertEqual(float(bars[0][4]),3414.5)
        self.assertEqual(float(bars[0][5]),30.0)

    def test_import_rejects_off_grid_minutes(self):
        import lzma
        import struct
        from datetime import date
        from .management.commands.import_dukascopy_xauusd import decode_day
        payload=lzma.compress(struct.pack(">IIIIIf",61,3400000,3400000,
            3399000,3401000,1),format=lzma.FORMAT_ALONE)
        with self.assertRaisesRegex(ValueError,"minute grid"):
            decode_day(payload,date(2026,10,27))

    def test_external_history_source_kept_separate_from_broker(self):
        from .management.commands.import_dukascopy_xauusd import SOURCE, SYMBOL
        self.assertNotEqual(SOURCE,"ramon-command-ea-1")
        self.assertEqual(SYMBOL,"XAUUSD")
