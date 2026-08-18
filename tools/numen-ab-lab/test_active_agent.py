import tempfile
import unittest
from pathlib import Path

from active_agent import (
    ActiveAgent,
    EventCursor,
    SurvivalPolicy,
    TriggerPolicy,
    fingerprint,
)
from action_brain import RecoveryRequired


class FakeContext:
    def __init__(self, events=None, status=None):
        self.events = list(events or [])   # 最新在前（同 get_recent_events 返回）
        self.status = status or {"hp": 20.0, "max_hp": 20.0, "hunger": 20, "saturation": 5.0}
        self.run_turns = []
        self._inbox = tempfile.TemporaryDirectory()
        self.inbox = __import__("event_inbox", fromlist=["EventInbox"]).EventInbox(
            Path(self._inbox.name) / "events.jsonl")

    def list_recent_events(self, count=50):
        return self.events[:count]

    def get_self_status(self):
        return self.status

    def run_turn(self, message):
        self.run_turns.append(message)
        return {"final": "自动回应", "actions": [], "history_before": 0, "history_after": 2}


def chat(detail, source="Wayne", kind="chat", time="1000"):
    return {"kind": kind, "source": source, "detail": detail, "time": time}


class EventCursorTest(unittest.TestCase):
    def test_load_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = EventCursor(Path(tmp) / "cursor.json")
            self.assertIsNone(cursor.load())
            cursor.save("fp-1")
            self.assertEqual("fp-1", EventCursor(Path(tmp) / "cursor.json").load())

    def test_no_tmp_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = EventCursor(Path(tmp) / "cursor.json")
            cursor.save("fp")
            self.assertFalse((Path(tmp) / "cursor.json.tmp").exists())

    def test_fingerprint_is_stable_and_field_order_independent(self):
        a = fingerprint({"kind": "chat", "source": "Wayne", "detail": "hi", "time": "1"})
        b = fingerprint({"time": "1", "detail": "hi", "source": "Wayne", "kind": "chat"})
        self.assertEqual(a, b)


class TriggerPolicyTest(unittest.TestCase):
    def test_mention_triggers(self):
        policy = TriggerPolicy("ABBrain", mention_words=["ABBrain", "八千代"])
        self.assertTrue(policy.should_trigger(chat("ABBrain 来帮个忙")))
        self.assertTrue(policy.should_trigger(chat("八千代 早上好")))

    def test_unrelated_chat_does_not_trigger(self):
        policy = TriggerPolicy("ABBrain", mention_words=["ABBrain", "八千代"])
        self.assertFalse(policy.should_trigger(chat("大家去砍树吧")))

    def test_own_death_triggers(self):
        policy = TriggerPolicy("ABBrain")
        self.assertTrue(policy.should_trigger(chat("", source="ABBrain", kind="death")))
        self.assertFalse(policy.should_trigger(chat("", source="Wayne", kind="death")))


class SurvivalPolicyTest(unittest.TestCase):
    def test_low_hp_or_hunger_needs_attention(self):
        policy = SurvivalPolicy(hp_threshold=50, hunger_threshold=6)
        self.assertTrue(policy.needs_attention({"hp": 30, "hunger": 20}))
        self.assertTrue(policy.needs_attention({"hp": 80, "hunger": 3}))
        self.assertFalse(policy.needs_attention({"hp": 80, "hunger": 18}))


class RecoveryContext(FakeContext):
    """run_turn 首次抛 RecoveryRequired，recover_turn 可配置失败次数。"""
    def __init__(self, events=None, status=None, recover_failures=0):
        super().__init__(events, status)
        self.recover_calls = 0
        self.mcp = FakeMcp()
        self.recover_failures = recover_failures
        self.cleared = False
        # 模拟 ActionBrainRuntime.checkpoint
        self.runtime = _FakeRuntime(_FakeCheckpoint(self))

    def run_turn(self, message):
        # checkpoint 存在（未清除）时每次 run_turn 都拒绝，模拟 RecoveryRequired
        if not self.cleared:
            raise RecoveryRequired("incomplete side-effect checkpoint exists")
        return super().run_turn(message)

    def recover_turn(self):
        self.recover_calls += 1
        if self.recover_failures > 0:
            self.recover_failures -= 1
            raise RecoveryRequired("recovery failed: bad tool args")
        return {"final": "已恢复中断回合", "actions": [],
                "history_before": 0, "history_after": 2}


class _FakeCheckpoint:
    def __init__(self, ctx):
        self.path = "/fake/checkpoint.json"
        self._ctx = ctx

    def clear(self):
        self._ctx.cleared = True


class _FakeRuntime:
    def __init__(self, ckpt):
        self.checkpoint = ckpt


class FakeMcp:
    def call(self, name, args):
        return {"lease_id": "lease-1"}


class RecoveryOnInterruptTest(unittest.TestCase):
    def test_interrupted_checkpoint_triggers_recovery_not_silent_skip(self):
        # 回归：长任务被外部终止后残留 checkpoint，_auto_turn 应自动 recover_turn
        # 而不是被 except:continue 静默吞掉导致假人永久沉默。
        with tempfile.TemporaryDirectory() as tmp:
            cursor = EventCursor(Path(tmp) / "cursor.json")
            cursor.save(fingerprint(chat("ABBrain 你好", time="1")))
            ctx = RecoveryContext(events=[chat("ABBrain 在吗", time="2")])
            agent = ActiveAgent(
                context_factory=lambda companion: ctx,
                cursor_store=cursor,
                poll_seconds=15, survival_interval=60, cooldown_seconds=120,
                clock=lambda: 1000,
            )
            result = agent.run_once("ABBrain")
            self.assertIsNotNone(result, "应产出结果而不是静默跳过")
            self.assertEqual(1, ctx.recover_calls, "应调用一次 recover_turn")
            self.assertFalse(ctx.cleared, "成功恢复不清除 checkpoint")

    def test_repeated_recovery_failure_clears_bad_checkpoint(self):
        # 回归：坏 checkpoint（模型生成无效参数）连续恢复失败 3 次后应被清除，
        # 否则假人会被死循环卡死、无法响应新任务。
        with tempfile.TemporaryDirectory() as tmp:
            cursor = EventCursor(Path(tmp) / "cursor.json")
            cursor.save(fingerprint(chat("ABBrain 你好", time="1")))
            # 每次 run_once 都会触发（游标每次被新事件推进）
            ctx = RecoveryContext(events=[chat("ABBrain 在吗", time="2")],
                                  recover_failures=99)
            agent = ActiveAgent(
                context_factory=lambda companion: ctx,
                cursor_store=cursor,
                poll_seconds=15, survival_interval=60, cooldown_seconds=120,
                clock=lambda: 1000,
            )
            # 需要多次触发以累加失败计数；直接调用 _auto_turn 或调整。
            # 这里模拟 3 次触发：每次给 agent 一个新事件推进游标
            for i in range(3):
                ctx.events = [chat("ABBrain 触发%d" % i, time=str(2 + i))]
                cursor.save(fingerprint(chat("ABBrain 你好", time="1")))
                try:
                    agent.run_once("ABBrain")
                except RecoveryRequired:
                    pass
            self.assertGreaterEqual(ctx.recover_calls, 3, "应尝试恢复至少3次")
            self.assertTrue(ctx.cleared, "连续失败3次后应清除 checkpoint")


class ActiveAgentTest(unittest.TestCase):
    def make_agent(self, ctx, cursor_store, **kwargs):
        defaults = dict(
            context_factory=lambda companion: ctx,
            cursor_store=cursor_store,
            poll_seconds=15,
            survival_interval=60,
            cooldown_seconds=120,
            clock=lambda: 1000,
        )
        defaults.update(kwargs)
        return ActiveAgent(**defaults)

    def test_first_run_establishes_cursor_without_triggering(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeContext(events=[chat("ABBrain 你好", time="1")])
            cursor = EventCursor(Path(tmp) / "cursor.json")
            agent = self.make_agent(ctx, cursor)
            result = agent.run_once("ABBrain")
            self.assertIsNone(result)
            self.assertEqual([], ctx.run_turns, "首次运行只设游标，不触发")
            self.assertEqual(fingerprint(chat("ABBrain 你好", time="1")), cursor.load())

    def test_mention_event_triggers_and_advances_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = EventCursor(Path(tmp) / "cursor.json")
            cursor.save(fingerprint(chat("ABBrain 你好", time="1")))
            ctx = FakeContext(events=[
                chat("ABBrain 在吗", time="3"),
                chat("无关消息", time="2"),
            ])
            agent = self.make_agent(ctx, cursor)
            result = agent.run_once("ABBrain")
            self.assertIsNotNone(result)
            self.assertEqual(1, len(ctx.run_turns))
            self.assertEqual(fingerprint(chat("ABBrain 在吗", time="3")), cursor.load())

    def test_unrelated_new_events_advance_cursor_without_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = EventCursor(Path(tmp) / "cursor.json")
            cursor.save(fingerprint(chat("旧消息", time="1")))
            ctx = FakeContext(events=[chat("新的无关消息", time="2")])
            agent = self.make_agent(ctx, cursor)
            result = agent.run_once("ABBrain")
            self.assertIsNone(result)
            self.assertEqual([], ctx.run_turns)
            self.assertEqual(fingerprint(chat("新的无关消息", time="2")), cursor.load())

    def test_cooldown_suppresses_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = EventCursor(Path(tmp) / "cursor.json")
            cursor.save(fingerprint(chat("ABBrain 你好", time="1")))
            ctx = FakeContext(events=[chat("ABBrain 又喊你", time="2")])
            clock = {"now": 1000}
            agent = self.make_agent(
                ctx, cursor, cooldown_seconds=120,
                clock=lambda: clock["now"])
            agent.run_once("ABBrain")          # 触发，记冷却
            ctx.events = [chat("ABBrain 第三次", time="3")]
            clock["now"] = 1100                # 100s < 120s 冷却
            result = agent.run_once("ABBrain")
            self.assertIsNone(result, "冷却期内不触发")
            clock["now"] = 1300                # 300s > 120s
            result = agent.run_once("ABBrain")
            self.assertIsNotNone(result, "冷却结束后可触发")

    def test_death_event_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = EventCursor(Path(tmp) / "cursor.json")
            cursor.save(fingerprint(chat("", time="1")))
            ctx = FakeContext(events=[chat("", source="ABBrain", kind="death", time="2")])
            agent = self.make_agent(ctx, cursor)
            result = agent.run_once("ABBrain")
            self.assertIsNotNone(result)
            self.assertIn("死亡", ctx.run_turns[0])

    def test_survival_check_triggers_on_low_hunger(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = EventCursor(Path(tmp) / "cursor.json")
            cursor.save(fingerprint(chat("ABBrain 你好", time="1")))
            ctx = FakeContext(
                events=[chat("无关", time="2")],
                status={"hp": 20.0, "max_hp": 20.0, "hunger": 3},
            )
            clock = {"now": 1000}
            agent = self.make_agent(
                ctx, cursor, survival_interval=60, clock=lambda: clock["now"])
            result = agent.run_once("ABBrain")
            # 事件不提及名字，但饥饿度 3 < 6 → 触发生存处理
            self.assertIsNotNone(result)
            self.assertIn("生存", ctx.run_turns[-1])
            # 冷却期内不再触发
            ctx.events = [chat("ABBrain 又喊你", time="3")]
            clock["now"] = 1050
            result = agent.run_once("ABBrain")
            self.assertIsNone(result, "冷却期内不触发")

    def test_no_new_events_and_healthy_no_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = EventCursor(Path(tmp) / "cursor.json")
            cursor.save(fingerprint(chat("ABBrain 你好", time="1")))
            ctx = FakeContext(events=[], status={"hp": 80, "hunger": 18})
            agent = self.make_agent(ctx, cursor)
            result = agent.run_once("ABBrain")
            self.assertIsNone(result)
            self.assertEqual([], ctx.run_turns)

    def test_cursor_at_latest_event_means_no_new_events(self):
        # 回归：游标已是最新一条时，更旧的历史事件绝不能再次触发。
        with tempfile.TemporaryDirectory() as tmp:
            latest = chat("ABBrain 你好", time="3")
            cursor = EventCursor(Path(tmp) / "cursor.json")
            cursor.save(fingerprint(latest))
            ctx = FakeContext(events=[latest, chat("旧消息", time="2")])
            agent = self.make_agent(ctx, cursor)
            result = agent.run_once("ABBrain")
            self.assertIsNone(result, "游标=最新时旧事件不算新事件")
            self.assertEqual([], ctx.run_turns)


if __name__ == "__main__":
    unittest.main()
