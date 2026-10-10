"""验证空消息失败回执重试状态与同步进程安全。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from action_brain import ActionBrainRuntime,TurnCheckpoint,TurnCancelled
from persistent_brain import BrainSessionStore
class GuidancePersistenceAuditTest(unittest.TestCase):
    def test_failed_model_after_guidance_keeps_guidance_in_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            ck=TurnCheckpoint(Path(tmp)/'ck')
            ck.write({'state':'tool_completed','turn_id':'t','user':'go','transcript':[{'role':'user','content':'go'}]})
            def model(*_):raise RuntimeError('provider offline')
            runtime=ActionBrainRuntime(store=BrainSessionStore(Path(tmp),'s','c'),checkpoint=ck,
                mcp=None,companion='c',model=model,tools=[],requires_control={})
            with self.assertRaises(RuntimeError):runtime.recover_turn(guidance='[Wayne] eat first')
            self.assertIn('eat first',str(ck.read()['transcript']))
if __name__=='__main__':unittest.main()
