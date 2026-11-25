from unittest.mock import patch
from django.test import SimpleTestCase
from ramon_pipeline.signalflow.scalp_lab import optimize
from ramon_pipeline.signalflow.scalping import ScalpCosts


class ScalpLabTests(SimpleTestCase):
    def test_no_candidate_does_not_promote_and_training_is_isolated(self):
        m1=[{'time':60*i} for i in range(1,1001)]
        m5=[{'time':300*i} for i in range(1,201)]
        with patch('ramon_pipeline.signalflow.scalp_lab.replay_scalper',
                   return_value={'trades':[], 'trade_count':0,'net_r':0,'expectancy_r':None}) as replay:
            result=optimize(m1,m5,costs=ScalpCosts(.01,42,5,5),tick_size=.01)
        self.assertEqual(result['status'],'NO_VALIDATED_CANDIDATE')
        self.assertIsNone(result['selected'])
        self.assertTrue(all(len(call.args[0])==700 for call in replay.call_args_list[:-2]))
        self.assertEqual(replay.call_args_list[-1].kwargs['score_start'],result['split_time']+60)
