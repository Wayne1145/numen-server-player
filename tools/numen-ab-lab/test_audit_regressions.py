"""审计边界回归：使用临时持久文件，不触碰正式世界。"""
import json
import tempfile
import unittest
from pathlib import Path
from persistent_brain import BrainSessionStore

from active_agent import ActiveAgent, EventCursor, fingerprint
from test_active_agent import FakeContext, chat
from action_brain import ActionBrainRuntime, TurnCheckpoint
from types import SimpleNamespace


class AgentAuditTest(unittest.TestCase):
    def test_cancel_then_new_chat_runs_only_new_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx=FakeContext(events=[chat('@yachiyo 新指令',time='4'),
                chat('@yachiyo cancel',kind='cancel',time='3'),chat('@yachiyo 旧指令',time='2')])
            cursor=EventCursor(Path(tmp)/'cursor');cursor.save(fingerprint(chat('旧',time='1')))
            agent=ActiveAgent(context_factory=lambda _:ctx,cursor_store=cursor)
            agent._send_reply=lambda *_:None
            agent.run_once('yachiyo')
            self.assertEqual(1,len(ctx.run_turns))
            self.assertFalse(ctx.inbox.read())

    def test_cancel_consumes_old_chat_without_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = FakeContext(events=[chat('@yachiyo cancel', kind='cancel', time='3'),
                                      chat('@yachiyo 采铁', time='2')])
            ctx.runtime = SimpleNamespace(checkpoint=TurnCheckpoint(root/'ck.json'))
            ctx.runtime.checkpoint.write({'state': 'tool_completed'})
            cursor = EventCursor(root/'cursor')
            cursor.save(fingerprint(chat('旧', time='1')))
            agent = ActiveAgent(context_factory=lambda _: ctx, cursor_store=cursor)
            agent._send_reply = lambda *_: None
            agent.run_once('yachiyo')
            self.assertEqual([], ctx.run_turns)
            self.assertFalse(ctx.runtime.checkpoint.path.exists())
            self.assertEqual(fingerprint(ctx.events[0]), cursor.load())


    def test_provider_failure_keeps_durable_input(self):
        class FailsOnce(FakeContext):
            def run_turn(self, message):
                self.run_turns.append(message)
                if len(self.run_turns) == 1:
                    raise RuntimeError('provider unavailable')
                return {'final': 'ok'}
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FailsOnce(events=[chat('@yachiyo 你好', time='2')])
            cursor = EventCursor(Path(tmp)/'cursor')
            cursor.save(fingerprint(chat('旧', time='1')))
            agent = ActiveAgent(context_factory=lambda _: ctx, cursor_store=cursor, cooldown_seconds=0)
            agent._send_reply = lambda *_: None
            with self.assertRaises(RuntimeError):
                agent.run_once('yachiyo')
            self.assertTrue(ctx.inbox.read())
            ctx.events = []
            cursor.path.with_suffix('.retry.json').unlink(missing_ok=True)
            agent.run_once('yachiyo')
            self.assertEqual(2, len(ctx.run_turns))
            self.assertEqual([], ctx.inbox.read())

    def test_empty_boot_does_not_skip_first_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeContext()
            cursor = EventCursor(Path(tmp)/'cursor')
            agent = ActiveAgent(context_factory=lambda _: ctx, cursor_store=cursor)
            agent._send_reply = lambda *_: None
            agent.run_once('yachiyo')
            ctx.events = [chat('@yachiyo 你好', time='2')]
            agent.run_once('yachiyo')
            self.assertEqual(1, len(ctx.run_turns))


class RuntimeAuditTest(unittest.TestCase):
    def test_sync_dispatched_receipt_recovers_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            ck=TurnCheckpoint(Path(tmp)/'ck')
            ck.write({'state':'tool_dispatched','turn_id':'sync','user':'move items',
                      'tool':'transfer','task_id':None,'dispatch':{'success':True,'message':'moved'},
                      'transcript':[{'role':'user','content':'move items'}]})
            calls=[]
            runtime=ActionBrainRuntime(store=BrainSessionStore(Path(tmp),'s','c'),checkpoint=ck,
                mcp=SimpleNamespace(call=lambda *a: calls.append(a)),companion='c',
                model=lambda *_:'{"type":"final","content":"done"}',tools=[],requires_control={})
            result=runtime.recover_turn()
            self.assertEqual('done',result['final'])
            self.assertEqual([],calls)

    def test_cancel_interrupts_waiting_model(self):
        import threading, time
        from action_brain import TurnCancelled
        entered, release = threading.Event(), threading.Event()
        started=time.monotonic()
        def model(*_):
            entered.set()
            release.wait(3)
            return '{"type":"final","content":"old"}'
        def boundary():
            if entered.is_set(): raise TurnCancelled('cancel')
            return ''
        with tempfile.TemporaryDirectory() as tmp:
            runtime=ActionBrainRuntime(store=BrainSessionStore(Path(tmp),'s','c'),
                checkpoint=TurnCheckpoint(Path(tmp)/'ck'), mcp=None, companion='c',model=model,
                tools=[],requires_control={},event_boundary=boundary)
            try:
                with self.assertRaises(TurnCancelled): runtime.run_turn('hello')
                self.assertLess(time.monotonic()-started,1.5)
            finally:release.set()

    def test_boundary_cancel_discards_generated_tool(self):
        from action_brain import TurnCancelled
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            cancelled = [False]
            def model(*_):
                cancelled[0] = True
                return json.dumps({'type':'tool','name':'goto','arguments':{'x':1,'z':1}})
            def boundary():
                if cancelled[0]:
                    raise TurnCancelled('cancelled')
                return ''
            runtime = ActionBrainRuntime(store=BrainSessionStore(Path(tmp),'s','c'),
                checkpoint=TurnCheckpoint(Path(tmp)/'ck'), mcp=SimpleNamespace(call=lambda *a: calls.append(a)),
                companion='c', model=model, tools=[], requires_control={'goto':True},
                event_boundary=boundary)
            with self.assertRaises(TurnCancelled):
                runtime.run_turn('go')
            self.assertEqual([], calls)

    def test_unknown_retained_task_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            def call(*_):
                raise RuntimeError('MCP tool call failed: unknown task_id: t1')
            runtime = ActionBrainRuntime(store=BrainSessionStore(Path(tmp),'s','c'),
                checkpoint=TurnCheckpoint(Path(tmp)/'ck'), mcp=SimpleNamespace(call=call),
                companion='c', model=None, tools=[], requires_control={})
            terminal = runtime._poll_existing_task('t1')['terminal']
            self.assertEqual('error', terminal['state'])
            self.assertTrue(terminal['requires_verification'])

    def test_unavailable_final_commits_and_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BrainSessionStore(Path(tmp), 's', 'c')
            ck = TurnCheckpoint(Path(tmp)/'ck')
            decisions = iter([{'type':'tool','name':'get_self_status','arguments':{}},
                              *[{'type':'tool','name':'missing','arguments':{}}]*3])
            mcp = SimpleNamespace(call=lambda *_: {'hp':20})
            runtime = ActionBrainRuntime(store=store, checkpoint=ck, mcp=mcp,
                companion='c', model=lambda *_: json.dumps(next(decisions)),
                tools=[], requires_control={'get_self_status':False})
            runtime.run_turn('hello')
            self.assertFalse(ck.path.exists())
            self.assertTrue(store.messages())


class ContextAuditTest(unittest.TestCase):
    def test_events_include_source_and_time(self):
        from persistent_action_cli import attach_events
        text = attach_events('hi', [{'kind':'chat','source':'Wayne','time':'123','detail':'@c hi'}])
        self.assertIn('Wayne', text)
        self.assertIn('123', text)

    def test_context_boundary_cancel_detected(self):
        from brain_server import CompanionContext
        from action_brain import TurnCancelled
        ctx = object.__new__(CompanionContext)
        ctx.companion = 'yachiyo'
        ctx._boundary_seen = set()
        ctx.get_self_status = lambda: {}
        ctx.list_recent_events = lambda **_: [chat('@yachiyo cancel', kind='cancel')]
        with self.assertRaises(TurnCancelled):
            ctx._event_boundary()

    def test_context_uuid_used_for_reads(self):
        from brain_server import CompanionContext
        ctx = object.__new__(CompanionContext)
        ctx.companion = 'yachiyo'
        ctx.companion_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        calls = []
        ctx.mcp = SimpleNamespace(call=lambda *a: calls.append(a) or {})
        ctx.get_self_status()
        ctx.list_recent_events()
        self.assertTrue(all(a[1]['companion'] == ctx.companion_id for a in calls))

    def test_already_committed_context_recovery_has_no_duplicate_reply(self):
        from brain_server import CompanionContext
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            context = object.__new__(CompanionContext)
            context.lock = threading.Lock()
            context.lock_path = Path(tmp)/'lock'
            context.store = BrainSessionStore(Path(tmp), 's', 'c')
            context.runtime = SimpleNamespace(recover_turn=lambda **_: {'state':'already_committed','turn_id':'t'})
            result = context.recover_turn()
            self.assertEqual('', result.get('final', ''))
            self.assertEqual('already_committed', result['state'])


class MemoryAuditTest(unittest.TestCase):
    def test_dimension_keys_and_stale_cache_preserve_both(self):
        from world_memory import WorldMemoryStore
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'memory'
            a, b = WorldMemoryStore(path), WorldMemoryStore(path)
            a.remember('minecraft:chest',1,2,3,dimension='minecraft:overworld')
            b.remember('minecraft:chest',1,2,3,dimension='minecraft:the_nether')
            self.assertEqual(2,len(WorldMemoryStore(path).recall(dimension=None)))
            self.assertEqual(1,len(a.recall(dimension='minecraft:overworld')))


class PersistenceAuditTest(unittest.TestCase):
    def test_torn_utf8_tail_is_repaired_before_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BrainSessionStore(Path(tmp), 's', 'c')
            store.path.parent.mkdir(parents=True, exist_ok=True)
            store.path.write_bytes(b'{"content":"\xe4\xb8')
            store.append_pair('hello', 'reply')
            restored = BrainSessionStore(Path(tmp), 's', 'c')
            self.assertEqual(2, len(restored.messages()))
            self.assertTrue(list(store.path.parent.glob('*.corrupt-*')))

    def test_stale_store_does_not_duplicate_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = BrainSessionStore(Path(tmp), 's', 'c')
            b = BrainSessionStore(Path(tmp), 's', 'c')
            messages = [{'role': 'user', 'content': 'hello'}]
            b.append_batch(messages, transaction_id='turn')
            a.append_batch(messages, transaction_id='turn')
            self.assertEqual(1, len(BrainSessionStore(Path(tmp), 's', 'c').messages()))

if __name__ == '__main__':
    unittest.main()
