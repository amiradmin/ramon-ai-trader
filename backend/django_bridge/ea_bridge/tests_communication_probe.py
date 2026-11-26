from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from django.http import JsonResponse
from django.test import SimpleTestCase, RequestFactory
from .communication_probe import advance, new_samples, report, next_with_probe

class CommunicationProbeTests(SimpleTestCase):
    def setUp(self):
        self.now=datetime(2026,11,26,tzinfo=timezone.utc)
        self.samples=new_samples(10,self.now)

    def test_not_due(self):
        self.assertEqual(advance(self.samples,self.now,None,None),(False,None))

    def test_claim_and_echo(self):
        t=self.now+timedelta(seconds=1.2)
        accepted,ident=advance(self.samples,t,None,None)
        self.assertFalse(accepted)
        self.assertEqual(ident,self.samples[0]["id"])
        self.assertEqual(advance(self.samples,t+timedelta(milliseconds=500),ident,42),(True,None))
        self.assertEqual(self.samples[0]["poll_http_ms"],42)
        self.assertEqual(advance(self.samples,t+timedelta(milliseconds=600),ident,999),(True,None))
        self.assertEqual(self.samples[0]["poll_http_ms"],42)

    def test_one_outstanding(self):
        advance(self.samples,self.now+timedelta(seconds=1),None,None)
        self.assertEqual(advance(self.samples,self.now+timedelta(seconds=3),None,None),(False,None))

    def test_unknown_echo(self):
        self.assertEqual(advance(self.samples,self.now,"unknown",12),(False,None))

    def test_expired_sample(self):
        advance(self.samples,self.now+timedelta(seconds=1),None,None)
        self.assertFalse(advance(self.samples,self.now+timedelta(seconds=12),self.samples[0]["id"],42)[0])
        self.assertIsNone(self.samples[0]["echoed_at"])

    def test_invalid_rtt(self):
        t=self.now+timedelta(seconds=1)
        _,ident=advance(self.samples,t,None,None)
        self.assertTrue(advance(self.samples,t+timedelta(milliseconds=500),ident,-1)[0])
        self.assertIsNone(self.samples[0]["poll_http_ms"])

    def test_completion_and_timeout(self):
        run=SimpleNamespace(samples=self.samples,run_id="test",started_at=self.now,deadline=self.now+timedelta(seconds=30))
        result=report(run,self.now+timedelta(seconds=30))
        self.assertEqual(result["status"],"COMPLETE")
        self.assertEqual(result["counts"]["TIMED_OUT"],10)
        self.assertFalse(result["affects_orders"])

    @patch("ea_bridge.communication_probe.next_command")
    def test_trading_response_unchanged(self,original):
        expected=JsonResponse({"ok":True,"command":{"action":"OPEN","command_id":"example"}})
        original.return_value=expected
        request=RequestFactory().post("/commands/next",data={"probe_protocol":"ramon-probe-v1"},content_type="application/json")
        self.assertIs(next_with_probe(request),expected)

    @patch("ea_bridge.communication_probe.next_command")
    def test_legacy_client_unchanged(self,original):
        expected=JsonResponse({"ok":True,"command":None})
        original.return_value=expected
        request=RequestFactory().post("/commands/next",data={},content_type="application/json")
        self.assertIs(next_with_probe(request),expected)
