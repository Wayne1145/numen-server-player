"""新一轮边界审读回归。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from action_brain import ActionBrainRuntime, TurnCheckpoint, TurnCancelled
from persistent_brain import BrainSessionStore
class AdditionalAuditTest(unittest.TestCase):
    def test_checkpoint_keeps_generation_and_recovery_refuses_cancelled_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            ck=TurnCheckpoint(Path(tmp)/'ck')
            ck.write({'state':'tool_completed','turn_id':'t','user':'go','action_generation':123,
                      'transcript':[{'role':'user','content':'go'}]})
            runtime=ActionBrainRuntime(store=BrainSessionStore(Path(tmp),'s','c'),checkpoint=ck,
                mcp=None,companion='c',model=lambda *_:'{"type":"final","content":"old"}',tools=[],requires_control={})
            runtime.action_generation=124
            with self.assertRaises(TurnCancelled):runtime.recover_turn()
    def test_planned_recovery_records_sync_receipt_before_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            ck=TurnCheckpoint(Path(tmp)/'ck')
            ck.write({'state':'tool_planned','turn_id':'t','user':'transfer','tool':'transfer',
                      'arguments':{},'client_action_id':'key','transcript':[{'role':'user','content':'transfer'}]})
            def model(*_):
                self.assertEqual('tool_completed',ck.read()['state'])
                return '{"type":"final","content":"ok"}'
            runtime=ActionBrainRuntime(store=BrainSessionStore(Path(tmp),'s','c'),checkpoint=ck,
                mcp=SimpleNamespace(call=lambda name,args: {'lease_id':'L'} if name=='acquire_control' else {'success':True,'message':'ok'}),
                companion='c',model=model,tools=[],requires_control={'transfer':True})
            self.assertEqual('ok',runtime.recover_turn()['final'])
if __name__=='__main__':unittest.main()
