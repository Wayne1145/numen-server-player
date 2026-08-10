import tempfile
import unittest
from pathlib import Path

from persistent_action_cli import build_paths, create_model_adapter


class PersistentActionCliTest(unittest.TestCase):
    def test_paths_are_keyed_by_server_and_uuid_not_display_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one = build_paths(root, "numen-ab", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            two = build_paths(root, "numen-ab", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
            self.assertNotEqual(one["session"], two["session"])
            self.assertNotEqual(one["checkpoint"], two["checkpoint"])
            self.assertNotIn("ABBrain", str(one["session"]))

    def test_model_adapter_includes_persistent_history_and_tool_contract(self):
        observed = {}

        def transport(payload):
            observed.update(payload)
            return {
                "choices": [{"message": {"content": '{"type":"final","content":"记得"}'}}]
            }

        adapter = create_model_adapter(transport)
        history = [
            {"role": "user", "content": "口令是星潮-824"},
            {"role": "assistant", "content": "记住了"},
            {"role": "user", "content": "现在是什么口令？"},
        ]
        tools = [{"name": "get_self_status", "parameters": {"type": "object"}}]
        answer = adapter(history, tools)

        self.assertEqual('{"type":"final","content":"记得"}', answer)
        self.assertEqual(history, observed["messages"][1:])
        self.assertIn("<available_tools>", observed["messages"][0]["content"])
        self.assertIn("task_id", observed["messages"][0]["content"])
        self.assertIn("不得把 idle 当作成功", observed["messages"][0]["content"])

    def test_model_adapter_rejects_empty_response(self):
        adapter = create_model_adapter(
            lambda _payload: {"choices": [{"message": {"content": ""}}]}
        )
        with self.assertRaisesRegex(ValueError, "empty"):
            adapter([{"role": "user", "content": "你好"}], [])


if __name__ == "__main__":
    unittest.main()
