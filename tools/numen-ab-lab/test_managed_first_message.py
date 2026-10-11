"""全新同伴在大脑启动之后收到的首条消息不能被跳过。"""
import tempfile
import unittest
from pathlib import Path
from active_agent import ActiveAgent, EventCursor
from test_active_agent import FakeContext, chat

class ManagedFirstMessageTest(unittest.TestCase):
    def test_new_companion_first_message_after_start_is_consumed(self):
        with tempfile.TemporaryDirectory() as t:
            ctx=FakeContext(events=[chat('@Actor 你好', time='2000')])
            agent=ActiveAgent(context_factory=lambda _:ctx,cursor_store=EventCursor(Path(t)/'cursor.json'),cooldown_seconds=0,clock=lambda:100)
            agent.event_start_ms=1500
            result=agent.run_once('Actor')
            self.assertIsNotNone(result)
            self.assertEqual(len(ctx.run_turns),1)

if __name__=='__main__': unittest.main()
