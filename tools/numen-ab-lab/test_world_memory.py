import json
import tempfile
import unittest
from pathlib import Path

from world_memory import WorldMemoryStore


class WorldMemoryStoreTest(unittest.TestCase):
    def test_remember_and_recall_sorted_by_distance(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldMemoryStore(Path(tmp) / "memory.json")
            store.remember("minecraft:crafting_table", 10, 64, 0, note="我的工作台")
            store.remember("minecraft:chest", 2, 64, 3)
            store.remember("minecraft:crafting_table", 5, 64, 0)

            items = store.recall(x=0, z=0)
            self.assertEqual("minecraft:chest", items[0]["type"])
            self.assertEqual(2, items[0]["x"])
            self.assertEqual("minecraft:crafting_table", items[1]["type"])
            self.assertEqual(5, items[1]["x"])
            self.assertEqual("minecraft:crafting_table", items[2]["type"])
            self.assertEqual(10, items[2]["x"])

    def test_recall_filters_by_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldMemoryStore(Path(tmp) / "memory.json")
            store.remember("minecraft:crafting_table", 5, 64, 0)
            store.remember("minecraft:furnace", 8, 64, 0)

            items = store.recall(x=0, z=0, type="minecraft:furnace")
            self.assertEqual(1, len(items))
            self.assertEqual("minecraft:furnace", items[0]["type"])

    def test_remember_same_position_updates_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorldMemoryStore(Path(tmp) / "memory.json")
            store.remember("minecraft:chest", 2, 64, 3, note="旧")
            store.remember("minecraft:chest", 2, 64, 3, note="新备注")

            items = store.recall(x=0, z=0, type="minecraft:chest")
            self.assertEqual(1, len(items))
            self.assertEqual("新备注", items[0]["note"])

    def test_persists_across_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            WorldMemoryStore(path).remember("minecraft:chest", 2, 64, 3)

            reopened = WorldMemoryStore(path)
            items = reopened.recall(x=0, z=0)
            self.assertEqual(1, len(items))
            self.assertEqual("minecraft:chest", items[0]["type"])

    def test_no_tmp_file_left_after_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            store = WorldMemoryStore(path)
            store.remember("minecraft:chest", 2, 64, 3)
            self.assertFalse(Path(str(path) + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
