import tempfile
import unittest
from pathlib import Path

from event_inbox import EventInbox


class EventInboxTest(unittest.TestCase):
    def test_push_read_clear_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = EventInbox(Path(tmp) / "events.jsonl")
            inbox.push({"kind": "death", "detail": "fell"})
            inbox.push({"kind": "chat", "detail": "hello"})

            events = inbox.read()
            self.assertEqual(2, len(events))
            self.assertEqual("death", events[0]["kind"])
            self.assertEqual("hello", events[1]["detail"])

            inbox.clear()
            self.assertEqual([], inbox.read())

    def test_persists_across_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            EventInbox(path).push({"kind": "death", "detail": "fell"})

            reopened = EventInbox(path)
            self.assertEqual(1, len(reopened.read()))

    def test_clear_only_removes_events_not_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            inbox = EventInbox(path)
            inbox.push({"kind": "chat", "detail": "hi"})
            inbox.clear()
            self.assertTrue(path.exists(), "清空后文件保留，避免目录抖动")


if __name__ == "__main__":
    unittest.main()
