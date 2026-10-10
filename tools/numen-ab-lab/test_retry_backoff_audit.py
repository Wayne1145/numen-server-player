"""重试状态必须落盘并退避；无 checkpoint 输入可以安全重试而非每 poll 刷配额。"""
import tempfile
import unittest
from pathlib import Path
from active_agent import ActiveAgent, EventCursor, fingerprint
from test_active_agent import FakeContext, chat
class RetryBackoffAuditTest(unittest.TestCase):
    def test_failed_input_backoff_survives_agent_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            class Ctx(FakeContext):
                def run_turn(self,message):self.run_turns.append(message);raise RuntimeError('offline')
            ctx=Ctx(events=[chat('@yachiyo go',time='2')]);cursor=EventCursor(Path(tmp)/'cursor')
            cursor.save(fingerprint(chat('old',time='1')))
            make=lambda:ActiveAgent(context_factory=lambda _:ctx,cursor_store=cursor,clock=lambda:1000)
            with self.assertRaises(RuntimeError):make().run_once('yachiyo')
            make().run_once('yachiyo')
            self.assertEqual(1,len(ctx.run_turns))
if __name__=='__main__':unittest.main()
