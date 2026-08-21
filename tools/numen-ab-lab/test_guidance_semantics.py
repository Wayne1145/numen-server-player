"""测试：玩家任务进行中"插入引导"与"cancel 打断"语义。

覆盖 2026-08-21 新增的恢复注入（recover_turn(guidance=...)）与
run_once 的 cancel 清 checkpoint 优先级重排。
"""
import json
import tempfile
import unittest
from pathlib import Path

from active_agent import (
    ActiveAgent,
    EventCursor,
    TriggerPolicy,
    fingerprint,
    _guidance_from_events,
)
from action_brain import (
    ActionBrainRuntime,
    TurnCheckpoint,
    _guidance_message,
)
from persistent_brain import BrainSessionStore


class FakeMcp:
    """最小 MCP 替身：记录调用，返回可配置结果。"""

    def __init__(self, statuses=None):
        self.calls = []
        self.statuses = statuses or []

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "acquire_control":
            return {"lease_id": "lease-1"}
        if name == "release_control":
            return "released"
        if name == "task_status":
            if self.statuses:
                return self.statuses.pop(0)
            return {"state": "done", "task_id": "t", "result": {"success": True}}
        return {"success": True, "message": "ok"}


class GuidanceMessageTest(unittest.TestCase):
    def test_guidance_message_wraps_player_text(self):
        msg = _guidance_message("小心，那边有僵尸")
        self.assertIn("<player_guidance>", msg)
        self.assertIn("小心，那边有僵尸", msg)
        self.assertIn("不是新任务", msg)

    def test_guidance_from_events_strips_mention(self):
        events = [
            {"kind": "chat", "detail": "@yachiyo 先别挖了，把食物吃了"},
            {"kind": "chat", "detail": "@yachiyo 然后回来找我"},
        ]
        text = _guidance_from_events(events)
        self.assertIn("先别挖了，把食物吃了", text)
        self.assertIn("然后回来找我", text)
        self.assertNotIn("@yachiyo", text)


class RecoverTurnGuidanceTest(unittest.TestCase):
    """recover_turn(guidance=...) 注入引导，不打断旧回合。"""

    def _make_runtime(self, mcp, model, checkpoint_data, tmp):
        root = Path(tmp)
        store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
        checkpoint = TurnCheckpoint(root / "checkpoint.json")
        checkpoint.write(checkpoint_data)
        runtime = ActionBrainRuntime(
            store=store, checkpoint=checkpoint, mcp=mcp, companion="ABBrain",
            model=model,
            tools=[{"name": "goto", "parameters": {}}],
            requires_control={"goto": True},
            local_tools={},
            sleep=lambda _: None,
            max_rounds=8,
        )
        return runtime

    def test_recover_with_guidance_injects_player_message(self):
        """恢复时传入 guidance，模型应看到 <player_guidance> 消息。"""
        with tempfile.TemporaryDirectory() as tmp:
            seen_messages = []

            def model(messages, _tools):
                seen_messages.append(messages)
                return '{"type":"final","content":"好的，我先吃饭。"}'

            mcp = FakeMcp([{"state": "done", "task_id": "t9",
                            "result": {"success": True}}])
            runtime = self._make_runtime(
                mcp, model,
                {
                    "state": "tool_planned",
                    "turn_id": "turn-guidance",
                    "user": "去挖铁矿",
                    "tool": "goto",
                    "arguments": {"x": 1.0, "y": None, "z": 1.0},
                    "client_action_id": "act-g-1",
                    "transcript": [
                        {"role": "user", "content": "去挖铁矿"},
                        {"role": "assistant",
                         "content": '{"type":"tool","name":"goto","arguments":{}}'},
                    ],
                },
                tmp,
            )
            result = runtime.recover_turn(guidance="先吃饭再挖")

            self.assertEqual("好的，我先吃饭。", result["final"])
            # 模型最后一次看到的消息应含玩家引导
            last_messages = seen_messages[-1]
            joined = json.dumps(last_messages, ensure_ascii=False)
            self.assertIn("先吃饭再挖", joined)
            self.assertIn("<player_guidance>", joined)
            # 回合正常 commit，checkpoint 已清
            self.assertFalse(runtime.checkpoint.path.exists())
            # 已提交历史包含本次回合，且引导语在历史中（引导成为回合一部分）
            committed = runtime.store.messages()
            committed_text = json.dumps(committed, ensure_ascii=False)
            self.assertIn("先吃饭再挖", committed_text)

    def test_recover_without_guidance_has_no_guidance_message(self):
        """不传 guidance 时，transient 不应出现 <player_guidance>。"""
        with tempfile.TemporaryDirectory() as tmp:
            seen_messages = []

            def model(messages, _tools):
                seen_messages.append(messages)
                return '{"type":"final","content":"继续挖。"}'

            mcp = FakeMcp([{"state": "done", "task_id": "t9",
                            "result": {"success": True}}])
            runtime = self._make_runtime(
                mcp, model,
                {
                    "state": "tool_planned",
                    "turn_id": "turn-nog",
                    "user": "去挖铁矿",
                    "tool": "goto",
                    "arguments": {"x": 1.0, "y": None, "z": 1.0},
                    "client_action_id": "act-ng-1",
                    "transcript": [
                        {"role": "user", "content": "去挖铁矿"},
                        {"role": "assistant",
                         "content": '{"type":"tool","name":"goto","arguments":{}}'},
                    ],
                },
                tmp,
            )
            result = runtime.recover_turn()

            self.assertEqual("继续挖。", result["final"])
            joined = json.dumps(seen_messages[-1], ensure_ascii=False)
            self.assertNotIn("<player_guidance>", joined)


class RunOnceCancelAndGuidanceTest(unittest.TestCase):
    """run_once：cancel 清 checkpoint；玩家点名注入引导。"""

    class FakeCheckpoint:
        def __init__(self, path):
            self.path = path

        def clear(self):
            self.path.unlink(missing_ok=True)

    class FakeContext:
        def __init__(self, events, checkpoint_path):
            self._events = events
            self.companion_id = "uuid-1"
            self.runtime = type("R", (), {"checkpoint": checkpoint_path})()
            self.inbox = type("I", (), {"push": lambda self, e: None,
                                        "clear": lambda self: None})()
            self.recover_guidance = None

        def list_recent_events(self, count=50):
            return list(self._events)

        def get_self_status(self):
            return {"hp": 20, "hunger": 20}

        def recover_turn(self, guidance=None):
            self.recover_guidance = guidance
            return {"final": "收到，继续。", "actions": []}

        def run_turn(self, message):
            return {"final": "新回合。", "actions": []}

    def _agent(self, events, checkpoint_path):
        ctx = self.FakeContext(events, checkpoint_path)
        agent = ActiveAgent(
            context_factory=lambda companion: ctx,
            cursor_store=lambda name, c: EventCursor(
                checkpoint_path.path.parent / "cursor.json"),
            mention_words=["@yachiyo"],
        )
        agent._send_reply = lambda ctx_, final: None
        # 预置游标到事件之前（模拟已运行过、这批事件是"新事件"）
        cursor = EventCursor(checkpoint_path.path.parent / "cursor.json")
        cursor.save(fingerprint({"kind": "chat", "source": "prev",
                                 "detail": "prev", "time": "1"}))
        return agent

    def test_cancel_event_clears_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ckpt = self.FakeCheckpoint(root / "checkpoint.json")
            ckpt.path.write_text('{"state":"tool_completed"}')
            events = [
                {"kind": "cancel", "source": "Wayne",
                 "detail": "@yachiyo cancel", "time": "2000"},
            ]
            agent = self._agent(events, ckpt)
            # 先建游标（模拟已运行过），再清掉，让这批事件成为"新事件"
            agent.run_once("yachiyo")
            self.assertFalse(ckpt.path.exists())
            # 无 checkpoint 后，cancel 事件不再触发回合
            self.assertIsNone(agent.run_once("yachiyo") or None)

    def test_mention_with_checkpoint_injects_guidance_not_new_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ckpt = self.FakeCheckpoint(root / "checkpoint.json")
            ckpt.path.write_text('{"state":"tool_completed"}')
            events = [
                {"kind": "chat", "source": "Wayne",
                 "detail": "@yachiyo 先吃饭再挖", "time": "3000"},
            ]
            agent = self._agent(events, ckpt)
            ctx = agent._agent_ctx if hasattr(agent, "_agent_ctx") else None
            result = agent.run_once("yachiyo")
            self.assertIsNotNone(result)
            self.assertEqual("收到，继续。", result["final"])


if __name__ == "__main__":
    unittest.main()
