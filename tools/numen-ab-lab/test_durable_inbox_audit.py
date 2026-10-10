"""补充持久消息与动态取消的边界回归。"""
import tempfile
import unittest
from pathlib import Path
from active_agent import ActiveAgent, EventCursor, fingerprint
from test_active_agent import FakeContext, chat
from types import SimpleNamespace
from action_brain import TurnCheckpoint, RecoveryRequired
class DurableInboxAuditTest(unittest.TestCase):
    def test_failed_recovery_does_not_retry_same_turn_twice_per_poll(self):
        with tempfile.TemporaryDirectory() as tmp:
            ck=TurnCheckpoint(Path(tmp)/'ck');ck.write({'state':'tool_completed'})
            ctx=FakeContext(events=[chat('system',time='2')]); ctx.runtime=SimpleNamespace(checkpoint=ck)
            calls=[]
            def recover(**_):calls.append(1);raise RuntimeError('provider offline')
            ctx.recover_turn=recover
            cursor=EventCursor(Path(tmp)/'cursor');cursor.save(fingerprint(chat('old',time='1')))
            agent=ActiveAgent(context_factory=lambda _:ctx,cursor_store=cursor)
            agent.run_once('yachiyo')
            self.assertEqual(1,len(calls))
            self.assertEqual([],ctx.run_turns)
if __name__=='__main__':unittest.main()
