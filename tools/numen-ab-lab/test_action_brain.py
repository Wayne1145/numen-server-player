import tempfile
import unittest
import uuid
from pathlib import Path

from action_brain import (
    ActionBrainRuntime,
    CompanionRunLock,
    RecoveryRequired,
    TurnCheckpoint,
    execute_tool_exact,
    resolve_companion_id,
)
from persistent_brain import BrainSessionStore


class FakeMcp:
    def __init__(self, statuses=None):
        self.calls = []
        self.statuses = list(statuses or [])

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "acquire_control":
            return {"lease_id": "lease-1"}
        if name == "release_control":
            return "released"
        if name == "goto":
            return {"success": True, "data": {"task_id": "t9", "async": True}}
        if name == "task_status":
            return self.statuses.pop(0)
        if name == "get_self_status":
            return {"name": arguments["companion"], "position": {"x": 6.2, "y": 100, "z": 0.5}}
        return {"success": True}


class CompanionBindingTest(unittest.TestCase):
    def test_only_one_process_lock_can_own_a_companion_brain(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brain.lock"
            first = CompanionRunLock(path)
            second = CompanionRunLock(path)
            with first:
                with self.assertRaisesRegex(BlockingIOError, "already running"):
                    with second:
                        self.fail("第二个同伴大脑不应取得锁")
            with second:
                self.assertTrue(path.exists())

    def test_resolves_exact_name_to_uuid_from_live_listing(self):
        companion_id = str(uuid.uuid4())
        listing = f"Other id={uuid.uuid4()} [live]\nABBrain id={companion_id} [live]"
        self.assertEqual(companion_id, resolve_companion_id(listing, "ABBrain"))

    def test_rejects_missing_or_ambiguous_live_identity(self):
        same = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, "exactly one"):
            resolve_companion_id("Other id=" + same + " [live]", "ABBrain")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            resolve_companion_id(
                f"ABBrain id={same} [live]\nABBrain id={uuid.uuid4()} [live]", "ABBrain"
            )


class ExactTaskExecutionTest(unittest.TestCase):
    def test_release_failure_does_not_lose_dispatch_task_id(self):
        mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
        release_calls = 0
        original_call = mcp.call

        def flaky_release(name, arguments):
            nonlocal release_calls
            if name == "release_control":
                release_calls += 1
                raise RuntimeError("network hiccup on release")
            return original_call(name, arguments)

        mcp.call = flaky_release
        observed = []
        outcome = execute_tool_exact(
            mcp,
            companion="ABBrain",
            name="goto",
            arguments={"x": 6.5, "y": None, "z": 0.5},
            requires_control=True,
            sleep=lambda _: None,
            on_dispatched=lambda dispatch, task_id: observed.append(task_id),
        )

        self.assertEqual("t9", observed[0])
        self.assertEqual("t9", outcome["task_id"])
        self.assertEqual("done", outcome["terminal"]["state"])
        self.assertEqual(1, release_calls)

    def test_write_action_uses_ephemeral_lease_and_exact_terminal_task_id(self):
        mcp = FakeMcp([
            {"state": "running", "task_id": "t9", "result": None},
            {"state": "done", "task_id": "t9", "result": {"success": True}},
        ])
        outcome = execute_tool_exact(
            mcp,
            companion="ABBrain",
            name="goto",
            arguments={"x": 6.5, "y": None, "z": 0.5},
            requires_control=True,
            sleep=lambda _: None,
        )
        self.assertEqual("done", outcome["terminal"]["state"])
        self.assertEqual(
            ["acquire_control", "goto", "release_control", "task_status", "task_status"],
            [name for name, _ in mcp.calls],
        )
        status_calls = [args for name, args in mcp.calls if name == "task_status"]
        self.assertEqual(["t9", "t9"], [args["task_id"] for args in status_calls])

    def test_dispatch_hook_receives_task_id_before_terminal_polling(self):
        mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
        observed = []
        execute_tool_exact(
            mcp,
            companion="ABBrain",
            name="goto",
            arguments={"x": 6.5, "y": None, "z": 0.5},
            requires_control=True,
            sleep=lambda _: None,
            on_dispatched=lambda dispatch, task_id: observed.append((dispatch, task_id)),
        )
        self.assertEqual("t9", observed[0][1])
        self.assertEqual("t9", observed[0][0]["data"]["task_id"])

    def test_idle_is_not_accepted_as_terminal_success(self):
        mcp = FakeMcp([{"state": "idle", "task_id": None, "result": None}])
        with self.assertRaisesRegex(RuntimeError, "idle"):
            execute_tool_exact(
                mcp,
                companion="ABBrain",
                name="goto",
                arguments={"x": 6.5, "y": None, "z": 0.5},
                requires_control=True,
                sleep=lambda _: None,
                timeout_seconds=0.01,
            )


class PersistentActionTurnTest(unittest.TestCase):
    def test_checkpoint_write_fsyncs_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            import action_brain
            original = action_brain.fsync_dir
            synced = []
            action_brain.fsync_dir = lambda path: synced.append(path)
            try:
                checkpoint.write({"state": "tool_planned", "turn_id": "t"})
            finally:
                action_brain.fsync_dir = original
            self.assertEqual([root], synced)
            self.assertEqual({"state": "tool_planned", "turn_id": "t"}, checkpoint.read())

    def test_invalid_model_format_is_retried_without_persisting_correction_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            observed = []
            answers = iter([
                "我应该用普通文字回答",
                '{"type":"final","content":"已改为合法格式。"}',
            ])

            def model(messages, _tools):
                observed.append([dict(message) for message in messages])
                return next(answers)

            runtime = ActionBrainRuntime(
                store=store,
                checkpoint=TurnCheckpoint(root / "checkpoint.json"),
                mcp=FakeMcp(), companion="ABBrain", model=model,
                tools=[], requires_control={}, max_format_retries=2,
            )
            result = runtime.run_turn("请回答")

            self.assertEqual("已改为合法格式。", result["final"])
            self.assertEqual(2, len(observed))
            self.assertIn("格式错误", observed[1][-1]["content"])
            persisted_text = "\n".join(message["content"] for message in store.messages())
            self.assertNotIn("普通文字", persisted_text)
            self.assertNotIn("格式错误", persisted_text)

    def test_format_retry_exhaustion_before_tools_leaves_no_checkpoint_or_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=FakeMcp(), companion="ABBrain",
                model=lambda _messages, _tools: "仍然不是 JSON",
                tools=[], requires_control={}, max_format_retries=1,
            )
            with self.assertRaisesRegex(ValueError, "valid JSON decision"):
                runtime.run_turn("请回答")
            self.assertFalse(checkpoint.path.exists())
            self.assertEqual([], store.messages())

    def test_complete_action_turn_commits_full_transcript_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
            answers = iter([
                '{"type":"tool","name":"goto","arguments":{"x":6.5,"y":null,"z":0.5}}',
                '{"type":"final","content":"已经走到目标附近。"}',
            ])

            runtime = ActionBrainRuntime(
                store=store,
                checkpoint=checkpoint,
                mcp=mcp,
                companion="ABBrain",
                model=lambda messages, tools: next(answers),
                tools=[{"name": "goto", "parameters": {}}],
                requires_control={"goto": True},
                sleep=lambda _: None,
            )
            result = runtime.run_turn("向东走六格")

            self.assertEqual("已经走到目标附近。", result["final"])
            self.assertFalse(checkpoint.path.exists())
            persisted = store.messages()
            self.assertEqual("user", persisted[0]["role"])
            self.assertEqual("向东走六格", persisted[0]["content"])
            self.assertIn('"name": "goto"', persisted[1]["content"])
            self.assertEqual("user", persisted[2]["role"])
            self.assertTrue(persisted[2]["content"].startswith("<tool_result "))
            self.assertIn('"state": "done"', persisted[2]["content"])
            self.assertNotIn('"samples"', persisted[2]["content"])
            self.assertTrue(result["actions"][0]["samples"])
            self.assertEqual({"role": "assistant", "content": "已经走到目标附近。"}, persisted[3])

    def test_model_failure_before_any_action_leaves_no_history_or_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")

            def fail(_messages, _tools):
                raise RuntimeError("model unavailable")

            runtime = ActionBrainRuntime(
                store=store,
                checkpoint=checkpoint,
                mcp=FakeMcp(),
                companion="ABBrain",
                model=fail,
                tools=[],
                requires_control={},
            )
            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                runtime.run_turn("不要留下悬空消息")
            self.assertEqual([], store.messages())
            self.assertFalse(checkpoint.path.exists())

    def test_existing_incomplete_side_effect_checkpoint_blocks_silent_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({
                "state": "tool_dispatched",
                "user": "向东走六格",
                "tool": "goto",
                "task_id": "t9",
            })
            runtime = ActionBrainRuntime(
                store=store,
                checkpoint=checkpoint,
                mcp=FakeMcp(),
                companion="ABBrain",
                model=lambda _messages, _tools: '{"type":"final","content":"不该执行"}',
                tools=[],
                requires_control={},
            )
            with self.assertRaises(RecoveryRequired):
                runtime.run_turn("向东走六格")
            self.assertEqual([], store.messages())

    def test_recover_dispatched_task_queries_same_id_without_replaying_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({
                "state": "tool_dispatched",
                "turn_id": "turn-recover",
                "user": "向东走六格",
                "tool": "goto",
                "task_id": "t9",
                "dispatch": {"data": {"task_id": "t9"}},
                "transcript": [
                    {"role": "user", "content": "向东走六格"},
                    {"role": "assistant", "content": '{"type":"tool","name":"goto","arguments":{}}'},
                ],
            })
            mcp = FakeMcp([{"state": "done", "task_id": "t9", "result": {"success": True}}])
            runtime = ActionBrainRuntime(
                store=store,
                checkpoint=checkpoint,
                mcp=mcp,
                companion="ABBrain",
                model=lambda _messages, _tools: '{"type":"final","content":"恢复后确认完成。"}',
                tools=[{"name": "goto", "parameters": {}}],
                requires_control={"goto": True},
                sleep=lambda _: None,
            )
            result = runtime.recover_turn()

            self.assertEqual("恢复后确认完成。", result["final"])
            self.assertNotIn("goto", [name for name, _ in mcp.calls])
            self.assertEqual("t9", [args for name, args in mcp.calls if name == "task_status"][0]["task_id"])
            self.assertFalse(checkpoint.path.exists())

    def test_recover_clears_checkpoint_when_turn_was_already_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = BrainSessionStore(root / "sessions", "ab", "uuid-1")
            transcript = [
                {"role": "user", "content": "请求"},
                {"role": "assistant", "content": "完成"},
            ]
            store.append_batch(transcript, transaction_id="turn-committed")
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({
                "state": "committing",
                "turn_id": "turn-committed",
                "transcript": transcript,
            })
            runtime = ActionBrainRuntime(
                store=store, checkpoint=checkpoint, mcp=FakeMcp(), companion="ABBrain",
                model=lambda _messages, _tools: "", tools=[], requires_control={},
            )
            result = runtime.recover_turn()
            self.assertEqual("already_committed", result["state"])
            self.assertFalse(checkpoint.path.exists())
            self.assertEqual(transcript, store.messages())

    def test_recover_refuses_ambiguous_planned_action_without_dispatch_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = TurnCheckpoint(root / "checkpoint.json")
            checkpoint.write({"state": "tool_planned", "turn_id": "turn-x", "tool": "goto"})
            runtime = ActionBrainRuntime(
                store=BrainSessionStore(root / "sessions", "ab", "uuid-1"),
                checkpoint=checkpoint, mcp=FakeMcp(), companion="ABBrain",
                model=lambda _messages, _tools: "", tools=[], requires_control={},
            )
            with self.assertRaisesRegex(RecoveryRequired, "ambiguous"):
                runtime.recover_turn()


if __name__ == "__main__":
    unittest.main()
