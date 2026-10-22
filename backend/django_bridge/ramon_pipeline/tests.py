"""Unit tests for the pure-Python pipeline contracts and Django orchestration."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from django.test import TestCase

from .schemas import Envelope, EventKind, PipelineError
from .market_data import normalize_bar
from .redis_buffer import RedisStreamBuffer, STREAMS
from .recovery import RecoveryPolicy, parse_utc, should_quarantine
from .orchestration import evaluate_and_record
from .risk import RiskVerdict
from .strategy import StrategyDecision
from ea_bridge.models import OrderIntentRecord, RiskVerdictRecord, StrategyDecisionRecord


class FakeRedis:
    def __init__(self):
        self.calls = []
    def xadd(self, name, fields, *, maxlen=None, approximate=True):
        self.calls.append((name, fields, maxlen, approximate))
        return "1-0"


class PipelineContractTests(unittest.TestCase):
    def test_envelope_rejects_naive_timestamp(self):
        with self.assertRaises(PipelineError):
            Envelope(EventKind.TELEMETRY, "test", {}, occurred_at=datetime(2026, 1, 1))

    def test_envelope_rejects_non_json_payload(self):
        with self.assertRaises(PipelineError):
            Envelope(EventKind.TELEMETRY, "test", {"bad": object()})

    def test_bar_normalizes_utc_and_decimals(self):
        row = normalize_bar({"symbol": "XAUUSD_o", "timeframe": "m1",
            "timestamp": "2026-10-22T12:00:00+03:00",
            "open": "100.0", "high": "102", "low": "99", "close": "101", "volume": "2"})
        self.assertEqual(row["timestamp"], datetime(2026, 10, 22, 9, tzinfo=timezone.utc))
        self.assertEqual(row["close"], Decimal("101"))
        self.assertEqual(row["timeframe"], "M1")

    def test_bar_rejects_inconsistent_high_low(self):
        with self.assertRaises(PipelineError):
            normalize_bar({"symbol": "X", "timeframe": "M1", "timestamp": "2026-10-22T12:00:00Z",
                "open": 100, "high": 99, "low": 98, "close": 100, "volume": 1})

    def test_execution_stream_is_not_automatically_trimmed(self):
        redis = FakeRedis()
        buffer = RedisStreamBuffer(redis, maxlen=50)
        buffer.publish(Envelope(EventKind.EXECUTION_EVENT, "ea", {"event_id": "e-1"}))
        self.assertEqual(redis.calls[0][0], STREAMS["execution"])
        self.assertIsNone(redis.calls[0][2])

    def test_market_stream_uses_configured_trim_target(self):
        redis = FakeRedis()
        RedisStreamBuffer(redis, maxlen=50).publish(Envelope(EventKind.MARKET_BAR, "ea", {}))
        self.assertEqual(redis.calls[0][2], 50)

    def test_recovery_policy_quarantines_after_limit(self):
        policy = RecoveryPolicy(max_delivery_attempts=3)
        self.assertFalse(should_quarantine(2, policy))
        self.assertTrue(should_quarantine(3, policy))

    def test_parse_utc_rejects_naive_time(self):
        with self.assertRaises(ValueError):
            parse_utc("2026-10-22T12:00:00")


class FakeStrategy:
    def __init__(self, action="BUY", decision_id="decision-test-1"):
        self.action = action
        self.decision_id = decision_id

    def decide(self, market_snapshot):
        return StrategyDecision(self.decision_id, "XAUUSD_o", self.action, 0.9,
                                "unit_test", "test-policy-v1", {"sample": 1})


class ApprovedRiskPolicy:
    def evaluate(self, decision, account, market):
        return RiskVerdict(True, "APPROVED", 0.1, 99.0, 103.0)


class OrchestrationTests(TestCase):
    def test_missing_risk_policy_fails_closed_and_creates_no_intent(self):
        result = evaluate_and_record({}, {}, strategy_engine=FakeStrategy(),
                                     risk_policy=None, instance_id="ea-test-1")
        self.assertFalse(result["risk_approved"])
        self.assertEqual(result["risk_reason"], "RISK_POLICY_UNAVAILABLE")
        self.assertIsNone(result["intent_id"])
        self.assertEqual(OrderIntentRecord.objects.count(), 0)
        self.assertEqual(StrategyDecisionRecord.objects.count(), 1)
        self.assertEqual(RiskVerdictRecord.objects.count(), 1)

    def test_hold_decision_never_creates_intent(self):
        result = evaluate_and_record({}, {}, strategy_engine=FakeStrategy(action="HOLD"),
                                     risk_policy=ApprovedRiskPolicy(), instance_id="ea-test-1")
        self.assertEqual(result["risk_reason"], "NO_TRADE_ACTION")
        self.assertIsNone(result["intent_id"])
        self.assertEqual(OrderIntentRecord.objects.count(), 0)

    def test_approved_intent_is_deterministic_and_persisted_once(self):
        kwargs = {"market_snapshot": {"spread": 20}, "account_snapshot": {"equity": 1000},
                  "strategy_engine": FakeStrategy(), "risk_policy": ApprovedRiskPolicy(),
                  "instance_id": "ea-test-1"}
        first = evaluate_and_record(**kwargs)
        second = evaluate_and_record(**kwargs)
        self.assertTrue(first["risk_approved"])
        self.assertEqual(first["intent_id"], second["intent_id"])
        self.assertEqual(OrderIntentRecord.objects.count(), 1)
        intent = OrderIntentRecord.objects.get()
        self.assertEqual(intent.state, OrderIntentRecord.State.PENDING)
        self.assertEqual(intent.volume, Decimal("0.10000000"))
        self.assertFalse(first["mt5_delivery_enabled"])
