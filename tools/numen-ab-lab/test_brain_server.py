import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from brain_server import BrainServer, CompanionContext


class StubContext:
    def __init__(self, runtime=None, store=None, memory=None, inbox=None,
                 listing="ABBrain  id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa  [live]"):
        self.runtime = runtime
        self.store = store
        self.memory = memory
        self.inbox = inbox
        self.listing = listing
        self.resolved = None
        self.factory_persona = None

    def list_companions(self):
        return self.listing

    def run_turn(self, message):
        self.resolved = ("turn", message)
        return {"final": "完成", "actions": [], "history_before": 0, "history_after": 2}

    def recover_turn(self):
        self.resolved = ("recover",)
        return {"final": "已恢复", "actions": [], "history_before": 2, "history_after": 4}

    def history(self):
        return [{"role": "user", "content": "你好"}]

    def memory_blocks(self):
        return [{"type": "minecraft:chest", "x": 1, "y": 64, "z": 0}]

    def events(self):
        return [{"kind": "death", "detail": "fell"}]


class BrainServerTest(unittest.TestCase):
    def setUp(self):
        self.context = StubContext()
        self.server = BrainServer(
            host="127.0.0.1", port=0, token="test-token",
            factory=lambda server_id, companion, persona=None: (
                setattr(self.context, "factory_persona", persona), self.context)[1],
            static_dir=Path(__file__).resolve().parent / "webui",
        )
        self.server.start()
        self.url = f"http://127.0.0.1:{self.server.bound_port()}"

    def tearDown(self):
        self.server.stop()

    def _request(self, path, method="GET", body=None, token="test-token"):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())

    def test_health(self):
        status, body = self._request("/health")
        self.assertEqual(200, status)
        self.assertTrue(body["ok"])

    def test_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._request("/health", token=None)
        self.assertEqual(401, ctx.exception.code)

    def test_turn_endpoint(self):
        status, body = self._request("/v1/turns", method="POST", body={
            "server": "numen-ab", "companion": "ABBrain", "message": "去砍树",
            "persona": "yachiyo",
        })
        self.assertEqual(200, status)
        self.assertEqual("完成", body["final"])
        self.assertEqual(("turn", "去砍树"), self.context.resolved)
        self.assertEqual("yachiyo", self.context.factory_persona)

    def test_companion_context_enriches_smelting_request_for_active_agent(self):
        class Store:
            def should_compact(self, **_kwargs):
                return False

            def messages(self):
                return []

        class Runtime:
            def __init__(self):
                self.message = None

            def run_turn(self, message):
                self.message = message
                return {"final": "完成", "actions": []}

        class Inbox:
            def read(self):
                return []

            def clear(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            context = object.__new__(CompanionContext)
            context.lock = threading.Lock()
            context.lock_path = Path(tmp) / "companion.lock"
            context.store = Store()
            context.runtime = Runtime()
            context.inbox = Inbox()
            result = CompanionContext.run_turn(context, "帮我挖15个铁，烧成铁锭")

        self.assertEqual("完成", result["final"])
        self.assertIn("mandatory_prerequisites", context.runtime.message)
        self.assertIn("禁止先采目标矿", context.runtime.message)
        self.assertIn("最终产物必须保留在背包", context.runtime.message)

    def test_recover_endpoint(self):
        status, body = self._request("/v1/recover", method="POST", body={
            "server": "numen-ab", "companion": "ABBrain",
        })
        self.assertEqual(200, status)
        self.assertEqual("已恢复", body["final"])
        self.assertEqual(("recover",), self.context.resolved)

    def test_history_memory_events_endpoints(self):
        status, history = self._request("/v1/history?server=numen-ab&companion=ABBrain")
        self.assertEqual(200, status)
        self.assertEqual("你好", history["messages"][0]["content"])
        status, memory = self._request("/v1/memory?server=numen-ab&companion=ABBrain")
        self.assertEqual("minecraft:chest", memory["blocks"][0]["type"])
        status, events = self._request("/v1/events?server=numen-ab&companion=ABBrain")
        self.assertEqual("death", events["events"][0]["kind"])

    def test_unknown_path_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._request("/nope")
        self.assertEqual(404, ctx.exception.code)

    def test_webui_served_at_root(self):
        req = urllib.request.Request(self.url + "/", method="GET")
        req.add_header("Authorization", "Bearer test-token")
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(200, resp.status)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))


if __name__ == "__main__":
    unittest.main()
