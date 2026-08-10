import tempfile
import unittest
import urllib.error
from pathlib import Path

from brain_runner import (
    execute_tool_with_ephemeral_lease,
    inject_control_args,
    parse_decision,
    retry_transient,
    save_checkpoint,
    score_move_trace,
    tool_requires_control,
)


class ParseDecisionTest(unittest.TestCase):
    """模型每轮只能返回一个工具动作或最终回答。"""

    def test_parses_tool_action_from_plain_json(self):
        decision = parse_decision(
            '{"type":"tool","name":"goto","arguments":{"x":6,"z":0}}'
        )
        self.assertEqual("tool", decision["type"])
        self.assertEqual("goto", decision["name"])
        self.assertEqual({"x": 6, "z": 0}, decision["arguments"])

    def test_parses_final_answer_from_markdown_fence(self):
        decision = parse_decision(
            '```json\n{"type":"final","content":"已经到达。"}\n```'
        )
        self.assertEqual({"type": "final", "content": "已经到达。"}, decision)

    def test_rejects_unknown_decision_type(self):
        with self.assertRaisesRegex(ValueError, "unknown decision type"):
            parse_decision('{"type":"thought","content":"先想想"}')


class ResilienceTest(unittest.TestCase):
    def test_transient_502_retries_only_current_call(self):
        attempts = []

        def operation():
            attempts.append(1)
            if len(attempts) == 1:
                raise urllib.error.HTTPError("http://model", 502, "bad gateway", {}, None)
            return "ok"

        self.assertEqual("ok", retry_transient(operation, attempts=2, delay_seconds=0))
        self.assertEqual(2, len(attempts))

    def test_checkpoint_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.json"
            save_checkpoint(path, {"state": "incomplete", "rounds": [{"round": 1}]})
            self.assertEqual(
                {"state": "incomplete", "rounds": [{"round": 1}]},
                __import__("json").loads(path.read_text(encoding="utf-8")),
            )
            self.assertFalse((Path(tmp) / "trace.json.tmp").exists())


class ControlAndScoringTest(unittest.TestCase):
    def test_write_action_acquires_then_releases_ephemeral_lease(self):
        class FakeMcp:
            def __init__(self):
                self.calls = []

            def call(self, name, arguments):
                self.calls.append((name, arguments))
                if name == "acquire_control":
                    return {"lease_id": "lease-ephemeral"}
                return {"success": True}

        mcp = FakeMcp()
        result, lease_id = execute_tool_with_ephemeral_lease(
            mcp, companion="ABWalker", name="goto", arguments={"x": 6, "z": 0},
            requires_control=True,
        )
        self.assertEqual({"success": True}, result)
        self.assertEqual("lease-ephemeral", lease_id)
        self.assertEqual(
            [
                ("acquire_control", {"companion": "ABWalker"}),
                ("goto", {"x": 6, "z": 0, "companion": "ABWalker", "lease_id": "lease-ephemeral"}),
                ("release_control", {"companion": "ABWalker", "lease_id": "lease-ephemeral"}),
            ],
            mcp.calls,
        )

    def test_schema_lease_property_requires_control_even_when_not_required(self):
        schema = {
            "type": "object",
            "properties": {
                "companion": {"type": "string"},
                "lease_id": {"type": "string"},
                "x": {"type": "number"},
            },
            "required": ["companion"],
        }
        self.assertTrue(tool_requires_control(schema))

    def test_injects_companion_and_lease_without_overwriting_model_args(self):
        args = inject_control_args(
            {"x": 16, "z": -3}, companion="ABWalker", lease_id="lease-1",
            requires_control=True,
        )
        self.assertEqual(
            {"x": 16, "z": -3, "companion": "ABWalker", "lease_id": "lease-1"},
            args,
        )

    def test_move_score_requires_goto_status_verification_and_target_proximity(self):
        trace = {
            "tool_calls": ["goto", "get_self_status"],
            "target": {"x": 6.0, "z": 0.0},
            "final_status": {"position": {"x": 5.2, "z": 0.4}},
        }
        score = score_move_trace(trace, max_distance=2.5)
        self.assertTrue(score["passed"])
        self.assertLess(score["distance_to_target"], 2.5)

    def test_move_score_rejects_idle_without_status_verification(self):
        trace = {
            "tool_calls": ["goto"],
            "target": {"x": 6.0, "z": 0.0},
            "final_status": None,
        }
        score = score_move_trace(trace, max_distance=2.5)
        self.assertFalse(score["passed"])
        self.assertIn("get_self_status", score["missing_calls"])


if __name__ == "__main__":
    unittest.main()
