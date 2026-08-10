import tempfile
import unittest
from pathlib import Path

from persistent_action_cli import (
    attach_events,
    build_paths,
    create_model_adapter,
    load_persona,
    make_load_skill,
    make_world_memory_tools,
)


class PersistentActionCliTest(unittest.TestCase):
    def test_persona_is_loaded_from_personas_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            persona_dir = root / "personas"
            persona_dir.mkdir()
            (persona_dir / "yachiyo.md").write_text(
                "你是月见八千代：安静、温柔、疏离的守望者。", encoding="utf-8"
            )
            text = load_persona(root, "yachiyo")
            self.assertIn("月见八千代", text)
            self.assertIsNone(load_persona(root, "missing"))

    def test_model_adapter_injects_persona_before_system_prompt(self):
        observed = {}

        def transport(payload):
            observed.update(payload)
            return {
                "choices": [{"message": {"content": '{"type":"final","content":"好"}'}}]
            }

        adapter = create_model_adapter(transport, persona="你是安静的歌姬。")
        adapter([{"role": "user", "content": "你好"}], [])
        self.assertIn("你是安静的歌姬。", observed["messages"][0]["content"])

    def test_load_skill_returns_content_or_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp) / "skills"
            skills.mkdir()
            (skills / "mining.md").write_text("# 挖矿\n先确认工具。", encoding="utf-8")
            load_skill = make_load_skill(skills)

            ok = load_skill({"name": "mining"})
            self.assertTrue(ok["success"])
            self.assertIn("挖矿", ok["content"])
            missing = load_skill({"name": "nope"})
            self.assertFalse(missing["success"])

    def test_world_memory_tools_remember_and_recall(self):
        with tempfile.TemporaryDirectory() as tmp:
            from world_memory import WorldMemoryStore

            tools = make_world_memory_tools(WorldMemoryStore(Path(tmp) / "memory.json"))
            result = tools["remember_block"]({
                "type": "minecraft:crafting_table", "x": 5, "y": 64, "z": 0,
                "note": "家的工作台",
            })
            self.assertTrue(result["success"])
            recalled = tools["recall_blocks"]({"x": 0, "z": 0, "type": "minecraft:crafting_table"})
            self.assertEqual(1, len(recalled["blocks"]))
            self.assertEqual(5, recalled["blocks"][0]["x"])

    def test_attach_events_prefixes_user_message(self):
        events = [
            {"kind": "death", "detail": "fell from high place"},
            {"kind": "chat", "detail": "Wayne: 早上好"},
        ]
        merged = attach_events("去砍树", events)
        self.assertIn("fell from high place", merged)
        self.assertIn("早上好", merged)
        self.assertIn("去砍树", merged)
        self.assertEqual("去砍树", attach_events("去砍树", []))

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
