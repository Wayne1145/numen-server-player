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
