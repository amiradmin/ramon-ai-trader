from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from .arima_shadow import INTERVAL, accuracy_summary, model_configuration, process_forecast_cycle
from .models import ArimaShadowForecast, EACommand, EAHeartbeat, MarketBar, RiskVerdictRecord, StrategyDecisionRecord

BASE = datetime(2026, 11, 9, 8, tzinfo=dt_timezone.utc)
NOW = BASE+timedelta(seconds=15)
PREDICTION = {"forecast_close": 2013.0, "lower_95": 2010.0, "upper_95": 2016.0, "warning_count": 0}
FITTER = "ramon_pipeline.signalflow.forecasting.arima.forecast_next_close"


@override_settings(RAMON_ARIMA_SHADOW_ENABLED=True, RAMON_ARIMA_SHADOW_WINDOW=256)
class ArimaShadowLifecycleTests(TestCase):
    def setUp(self):
        self.feed = EAHeartbeat.objects.create(instance_id="shadow-demo", symbol="XAUUSD_o",
            status="TELEMETRY_OK", terminal_connected=True, payload={"account_trade_mode": 0})
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=NOW)
        MarketBar.objects.bulk_create([MarketBar(source=self.feed.instance_id, symbol=self.feed.symbol,
            timeframe="M15", timestamp=BASE-INTERVAL*(128-i),
            open=2000+i*.1, high=2001+i*.1, low=1999+i*.1, close=2000+i*.1) for i in range(128)])
        MarketBar.objects.update(received_at=BASE-timedelta(seconds=1))

    def cycle(self, now=NOW):
        with patch("ea_bridge.arima_shadow.timezone.now", return_value=now):
            return process_forecast_cycle(now=now)

    def target(self, close=2014):
        bar = MarketBar.objects.create(source=self.feed.instance_id, symbol=self.feed.symbol,
            timeframe="M15", timestamp=BASE, open=2013, high=max(close, 2015), low=min(close, 2010), close=close)
        MarketBar.objects.filter(pk=bar.pk).update(received_at=BASE+INTERVAL+timedelta(seconds=1))
        return bar

    @patch(FITTER, return_value=PREDICTION)
    def test_forecast_is_issued_once_before_its_outcome_and_does_not_create_orders(self, fit):
        self.assertEqual(self.cycle()["processed"], 1)
        self.assertEqual(self.cycle()["processed"], 0)
        row = ArimaShadowForecast.objects.get()
        self.assertEqual(row.status, "READY")
        self.assertEqual(row.target_bar_time, BASE)
        self.assertEqual(row.target_close_time, BASE+INTERVAL)
        self.assertLess(row.generated_at, row.target_close_time)
        self.assertEqual(row.observations, 128)
        self.assertEqual(len(row.input_sha256), 64)
        fit.assert_called_once()
        self.assertFalse(EACommand.objects.exists())
        self.assertFalse(StrategyDecisionRecord.objects.exists())
        self.assertFalse(RiskVerdictRecord.objects.exists())

    @patch(FITTER, return_value=PREDICTION)
    def test_future_forming_off_grid_and_other_source_candles_never_reach_the_fit(self, fit):
        for source, timeframe, stamp in [
            ("other", "M15", BASE-INTERVAL), (self.feed.instance_id, "M5", BASE-INTERVAL),
            (self.feed.instance_id, "M15", BASE),
            (self.feed.instance_id, "M15", BASE-INTERVAL+timedelta(seconds=1)),
            (self.feed.instance_id, "M15", BASE-INTERVAL+timedelta(microseconds=1)),
        ]:
            bar = MarketBar.objects.create(source=source, symbol=self.feed.symbol, timeframe=timeframe,
                timestamp=stamp, open=9000, high=9001, low=8999, close=9000)
            MarketBar.objects.filter(pk=bar.pk).update(received_at=BASE-timedelta(seconds=1))
        self.cycle()
        closes = fit.call_args.args[0]
        self.assertEqual(len(closes), 128)
        self.assertLess(max(closes), 9000)

    @patch(FITTER, return_value=PREDICTION)
    def test_only_the_exact_completed_target_is_scored_and_the_result_is_immutable(self, fit):
        self.cycle()
        target = self.target()
        self.assertEqual(self.cycle(BASE+INTERVAL-timedelta(seconds=1))["settled"], 0)
        self.assertEqual(self.cycle(BASE+INTERVAL+timedelta(seconds=5))["settled"], 1)
        row = ArimaShadowForecast.objects.get()
        self.assertEqual(row.status, "SCORED")
        self.assertEqual(row.actual_close, Decimal("2014"))
        MarketBar.objects.filter(pk=target.pk).update(close=2015)
        self.assertEqual(self.cycle(BASE+INTERVAL+timedelta(seconds=10))["settled"], 0)
        row.refresh_from_db()
        self.assertEqual(row.actual_close, Decimal("2014"))

    @patch(FITTER, return_value=PREDICTION)
    def test_no_historical_backfill_is_counted_as_a_forward_forecast(self, fit):
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=BASE+timedelta(minutes=5))
        self.cycle(BASE+timedelta(minutes=5))
        fit.assert_not_called()
        row = ArimaShadowForecast.objects.get()
        self.assertEqual((row.status, row.reason), ("SKIPPED", "WAITING_FOR_NEXT_CANDLE"))

    @patch(FITTER, return_value=PREDICTION)
    def test_next_candle_automatically_scores_previous_forecast_and_issues_a_new_one(self, fit):
        self.cycle()
        self.target()
        next_now = BASE+INTERVAL+timedelta(seconds=15)
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(last_seen=next_now)
        result = self.cycle(next_now)
        self.assertEqual(result, {"processed": 1, "settled": 1})
        self.assertEqual(ArimaShadowForecast.objects.filter(status="READY").count(), 1)
        self.assertEqual(ArimaShadowForecast.objects.filter(status="SCORED").count(), 1)
        self.assertEqual(fit.call_count, 2)
        self.assertFalse(EACommand.objects.exists())

    @patch(FITTER, return_value=PREDICTION)
    def test_fit_finishing_too_late_is_not_ready_or_scored(self, fit):
        with patch("ea_bridge.arima_shadow.timezone.now", return_value=BASE+INTERVAL):
            process_forecast_cycle(now=NOW)
        row = ArimaShadowForecast.objects.get()
        self.assertEqual((row.status, row.reason), ("SKIPPED", "MODEL_FINISHED_TOO_LATE"))

    @patch(FITTER, side_effect=ValueError("MODEL_DID_NOT_CONVERGE"))
    def test_nonconverged_models_are_explicit_failures_not_false_predictions(self, fit):
        self.cycle()
        row = ArimaShadowForecast.objects.get()
        self.assertEqual((row.status, row.reason), ("FAILED", "MODEL_DID_NOT_CONVERGE"))
        self.assertIsNone(row.forecast_close)
        self.assertFalse(EACommand.objects.exists())

    @patch(FITTER, return_value=PREDICTION)
    def test_missing_target_expires_without_substituting_a_later_candle(self, fit):
        self.cycle()
        bar = self.target(close=2099)
        MarketBar.objects.filter(pk=bar.pk).update(timestamp=BASE+INTERVAL)
        self.cycle(BASE+timedelta(hours=2))
        row = ArimaShadowForecast.objects.get()
        self.assertEqual((row.status, row.reason), ("SKIPPED", "TARGET_CANDLE_NOT_RECEIVED"))
        self.assertIsNone(row.actual_close)

    @patch(FITTER, return_value=PREDICTION)
    def test_insufficient_or_not_yet_received_history_is_not_fitted(self, fit):
        oldest = MarketBar.objects.order_by("timestamp").first()
        MarketBar.objects.filter(pk=oldest.pk).update(received_at=NOW+timedelta(seconds=1))
        self.cycle()
        fit.assert_not_called()
        self.assertEqual(ArimaShadowForecast.objects.get().reason, "INSUFFICIENT_CANDLE_HISTORY")

    @override_settings(RAMON_ARIMA_SHADOW_ENABLED=False)
    @patch(FITTER)
    def test_disabled_observer_does_no_work(self, fit):
        self.assertEqual(self.cycle(), {"processed": 0, "settled": 0})
        fit.assert_not_called()
        self.assertFalse(ArimaShadowForecast.objects.exists())

    @patch(FITTER)
    def test_non_demo_or_stale_feeds_do_not_start_models(self, fit):
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(payload={"account_trade_mode": 2})
        self.cycle()
        EAHeartbeat.objects.filter(pk=self.feed.pk).update(payload={"account_trade_mode": 0}, last_seen=NOW-timedelta(minutes=6))
        self.cycle()
        fit.assert_not_called()
        self.assertFalse(ArimaShadowForecast.objects.exists())

    def test_stuck_claims_are_marked_failed_after_worker_interruption(self):
        EAHeartbeat.objects.all().delete()
        _, version = model_configuration()
        row = ArimaShadowForecast.objects.create(source="shadow-demo", symbol="XAUUSD_o", model_version=version,
            origin_bar_time=BASE-INTERVAL, target_bar_time=BASE, target_close_time=BASE+INTERVAL,
            origin_close=2012, started_at=NOW-timedelta(minutes=3))
        self.cycle()
        row.refresh_from_db()
        self.assertEqual((row.status, row.reason), ("FAILED", "WORKER_INTERRUPTED_OR_TIMED_OUT"))

    @patch(FITTER, return_value=PREDICTION)
    def test_dashboard_reads_saved_forecasts_without_fitting_or_writing_decisions(self, fit):
        self.cycle()
        with patch("ea_bridge.forecast_views.timezone.now", return_value=NOW):
            response = self.client.get("/api/ea/v1/forecast/arima")
            second = self.client.get("/api/ea/v1/forecast/arima")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["state"], "FORECAST_ACTIVE")
        self.assertEqual(body["mode"], "OBSERVATION_ONLY")
        self.assertFalse(body["affects_orders"])
        self.assertEqual(body["accuracy"]["observations"], 0)
        self.assertEqual(body["latest"]["interval_direction"], "UNCERTAIN")
        self.assertEqual(body["source"], "shadow-demo")
        self.assertEqual(response.json(), second.json())
        fit.assert_called_once()
        self.assertFalse(EACommand.objects.exists())
        self.assertFalse(StrategyDecisionRecord.objects.exists())

    def test_dashboard_explains_first_start_and_renders_the_panel(self):
        body = self.client.get("/api/ea/v1/forecast/arima").json()
        self.assertEqual(body["state"], "WAITING_FOR_FIRST_FORECAST")
        self.assertEqual(body["accuracy"]["comparison"], "COLLECTING_RESULTS")
        response = self.client.get("/api/ea/v1/dashboard")
        self.assertContains(response, 'id="arimaTitle"')
        self.assertContains(response, "OBSERVATION ONLY")
        self.assertEqual(self.client.post("/api/ea/v1/forecast/arima").status_code, 405)

    @patch(FITTER, return_value=PREDICTION)
    def test_accuracy_excludes_late_older_version_and_other_feed_records(self, fit):
        self.cycle()
        self.target()
        self.cycle(BASE+INTERVAL+timedelta(seconds=5))
        good = ArimaShadowForecast.objects.get()
        for changes in ({"source": "other"}, {"model_version": "older-version"},
                        {"origin_bar_time": BASE-2*INTERVAL, "generated_at": BASE+INTERVAL+timedelta(seconds=1)}):
            fields = {field.name: getattr(good, field.name) for field in good._meta.fields if field.name != "id"}
            ArimaShadowForecast.objects.create(**{**fields, **changes})
        body = self.client.get("/api/ea/v1/forecast/arima").json()
        self.assertEqual(body["accuracy"]["observations"], 1)
        self.assertEqual(body["accuracy"]["mae"], 1)


class ArimaAccuracyTests(TestCase):
    def test_accuracy_uses_same_target_for_model_and_unchanged_price_baseline(self):
        rows = [SimpleNamespace(origin_close=Decimal(100), forecast_close=Decimal(102), actual_close=Decimal(103),
                                lower_95=Decimal(99), upper_95=Decimal(104)) for _ in range(20)]
        result = accuracy_summary(rows)
        self.assertEqual(result["mae"], 1)
        self.assertEqual(result["rmse"], 1)
        self.assertEqual(result["baseline_mae"], 3)
        self.assertEqual(result["directional_accuracy"], 1)
        self.assertEqual(result["interval_coverage"], 1)
        self.assertEqual(result["comparison"], "BEATS_BASELINE")
        self.assertEqual(accuracy_summary(rows[:1])["comparison"], "COLLECTING_RESULTS")

    def test_zero_result_state_does_not_invent_an_accuracy(self):
        result = accuracy_summary([])
        self.assertEqual(result["observations"], 0)
        self.assertIsNone(result["mae"])
        self.assertIsNone(result["directional_accuracy"])

    @override_settings(RAMON_ARIMA_SHADOW_WINDOW=127)
    def test_invalid_training_window_is_rejected(self):
        with self.assertRaises(ValueError):
            model_configuration()
