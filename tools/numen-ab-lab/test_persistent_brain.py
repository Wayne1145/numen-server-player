import json
import tempfile
import unittest
import uuid
from pathlib import Path

from persistent_brain import BrainSessionStore, run_persistent_turn


class BrainSessionStoreTest(unittest.TestCase):
    def test_batch_is_one_jsonl_transaction_and_reloads_as_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BrainSessionStore(Path(tmp), "ab-server", "companion")
            batch = [
                {"role": "user", "content": "去东边"},
                {"role": "assistant", "content": "调用 goto"},
                {"role": "user", "content": "工具完成"},
                {"role": "assistant", "content": "已经到达"},
            ]
            store.append_batch(batch)

            self.assertEqual(1, len(store.path.read_text(encoding="utf-8").splitlines()))
            reopened = BrainSessionStore(Path(tmp), "ab-server", "companion")
            self.assertEqual(batch, reopened.messages())

    def test_transaction_id_makes_batch_commit_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BrainSessionStore(Path(tmp), "ab-server", "companion")
            batch = [{"role": "user", "content": "一次"}]
            store.append_batch(batch, transaction_id="turn-1")
            store.append_batch(batch, transaction_id="turn-1")

            reopened = BrainSessionStore(Path(tmp), "ab-server", "companion")
            self.assertTrue(reopened.has_transaction("turn-1"))
            self.assertEqual(batch, reopened.messages())

    def test_reloads_history_for_same_server_and_companion(self):
        with tempfile.TemporaryDirectory() as tmp:
            companion = str(uuid.uuid4())
            first = BrainSessionStore(Path(tmp), "ab-server", companion)
            first.append("user", "基地叫月读庭园")
            first.append("assistant", "记住了")

            reopened = BrainSessionStore(Path(tmp), "ab-server", companion)
            self.assertEqual(
                [
                    {"role": "user", "content": "基地叫月读庭园"},
                    {"role": "assistant", "content": "记住了"},
                ],
                reopened.messages(),
            )

    def test_companions_are_strictly_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = BrainSessionStore(Path(tmp), "ab-server", "companion-one")
            two = BrainSessionStore(Path(tmp), "ab-server", "companion-two")
            one.append("user", "只属于一号的记忆")
            two.append("user", "只属于二号的记忆")

            self.assertEqual("只属于一号的记忆", one.messages()[0]["content"])
            self.assertEqual("只属于二号的记忆", two.messages()[0]["content"])
            self.assertNotEqual(one.path, two.path)

    def test_ignores_only_corrupt_trailing_jsonl_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BrainSessionStore(Path(tmp), "ab-server", "companion")
            store.append("user", "完整记录")
            with store.path.open("a", encoding="utf-8") as handle:
                handle.write('{"role":"assistant"')

            reopened = BrainSessionStore(Path(tmp), "ab-server", "companion")
            self.assertEqual([{"role": "user", "content": "完整记录"}], reopened.messages())

    def test_rejects_invalid_role_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BrainSessionStore(Path(tmp), "ab-server", "companion")
            with self.assertRaisesRegex(ValueError, "invalid role"):
                store.append("system", "不能混进动态历史")
            self.assertFalse(store.path.exists())

    def test_successful_turn_persists_user_and_assistant_atomically_as_a_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BrainSessionStore(Path(tmp), "ab-server", "companion")
            observed = []

            def model(messages):
                observed.extend(messages)
                return "我记住了"

            reply = run_persistent_turn(store, "我的名字是 Wayne", model)
            self.assertEqual("我记住了", reply)
            self.assertEqual([{"role": "user", "content": "我的名字是 Wayne"}], observed)
            self.assertEqual(
                [
                    {"role": "user", "content": "我的名字是 Wayne"},
                    {"role": "assistant", "content": "我记住了"},
                ],
                store.messages(),
            )

    def test_failed_model_call_does_not_persist_half_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BrainSessionStore(Path(tmp), "ab-server", "companion")

            def fail(_messages):
                raise RuntimeError("model unavailable")

            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                run_persistent_turn(store, "这一句不能成为悬空问题", fail)
            self.assertEqual([], store.messages())
            self.assertFalse(store.path.exists())


if __name__ == "__main__":
    unittest.main()
