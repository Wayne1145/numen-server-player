"""实时循环的失败 checkpoint 不应在同一 poll 落入新任务，且不抹除。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from active_agent import ActiveAgent,EventCursor,fingerprint
from action_brain import TurnCheckpoint
from test_active_agent import FakeContext,chat
class RecoverySafetyAuditTest(unittest.TestCase):
    def test_pending_recovery_failure_cannot_fallthrough_to_new_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx=FakeContext(events=[chat('@yachiyo 指引',time='2')])
            ck=TurnCheckpoint(Path(tmp)/'ck');ck.write({'state':'tool_completed'})
            ctx.runtime=SimpleNamespace(checkpoint=ck)
            calls=[]
            def recover(**_):calls.append(1);raise RuntimeError('offline')
            ctx.recover_turn=recover
            cursor=EventCursor(Path(tmp)/'cursor');cursor.save(fingerprint(chat('old',time='1')))
            agent=ActiveAgent(context_factory=lambda _:ctx,cursor_store=cursor)
            agent.run_once('yachiyo')
            self.assertEqual(1,len(calls))
            self.assertEqual([],ctx.run_turns)
            self.assertTrue(ck.path.exists())
    def test_raced_checkpoint_failure_is_retained_after_three_attempts(self):
        # 回合启动前快照尚未出现，但启动时发现未完任务的竞态分支。
        from action_brain import RecoveryRequired
        with tempfile.TemporaryDirectory() as tmp:
            ctx=FakeContext()
            ck=TurnCheckpoint(Path(tmp)/'ck')
            ctx.runtime=SimpleNamespace(checkpoint=ck)
            def run(_):
                ck.write({'state':'tool_planned','turn_id':'original'})
                raise RecoveryRequired('pending')
            def recover(**_):raise RuntimeError('provider unavailable')
            ctx.run_turn=run
            ctx.recover_turn=recover
            agent=ActiveAgent(context_factory=lambda _:ctx,cursor_store=EventCursor(Path(tmp)/'cursor'))
            for attempt in range(4):
                with self.assertRaises(RuntimeError):
                    agent._auto_turn(ctx,'yachiyo',[],float(attempt),message='go')
                self.assertTrue(ck.path.exists(),'临时失败不得删除未完成回合')
                self.assertEqual('original',ck.read()['turn_id'])

if __name__=='__main__':unittest.main()
