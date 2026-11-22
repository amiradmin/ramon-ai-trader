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
