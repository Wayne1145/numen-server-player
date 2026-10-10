"""检查整链收尾与插话消费，防止新消息再次变成新任务。"""
import tempfile
import unittest
from pathlib import Path
from active_agent import ActiveAgent, EventCursor, fingerprint
from test_active_agent import FakeContext, chat
class GuidanceCursorAuditTest(unittest.TestCase):
    def test_events_observed_during_turn_are_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor=EventCursor(Path(tmp)/'cursor');cursor.save(fingerprint(chat('old',time='1')))
            class Ctx(FakeContext):
                def run_turn(self,message):
                    self.run_turns.append(message)
                    self.events=[chat('@yachiyo guidance',time='3'),*self.events]
                    self._consumed_event_fingerprints={fingerprint(e) for e in self.events}
                    return {'final':'ok'}
            ctx=Ctx(events=[chat('@yachiyo go',time='2')])
            agent=ActiveAgent(context_factory=lambda _:ctx,cursor_store=cursor,cooldown_seconds=0)
            agent._send_reply=lambda *_:None
            agent.run_once('yachiyo');agent.run_once('yachiyo')
            self.assertEqual(1,len(ctx.run_turns))
if __name__=='__main__':unittest.main()
