#!/usr/bin/env python3
"""常驻大脑服务：HTTP API + Web UI，管理多服务器上的 Numen 同伴。"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import secrets
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from action_brain import (
    ActionBrainRuntime,
    CompanionRunLock,
    TurnCheckpoint,
    TurnCancelled,
    resolve_companion_id,
)
from brain_runner import McpClient, model_tools
from event_inbox import EventInbox
from persistent_brain import BrainSessionStore
from persistent_action_cli import (
    COMPACT_MAX_CHARS,
    COMPACT_MAX_MESSAGES,
    ROOT,
    attach_events,
    build_paths,
    compact_history,
    create_compactor,
    create_model_adapter,
    enrich_task_message,
    live_transport,
    load_persona,
    make_load_skill,
    make_world_memory_tools,
)
from world_memory import WorldMemoryStore


class CompanionContext:
    """一个同伴的运行上下文：MCP 连接、会话、记忆、收件箱与运行时。"""

    def __init__(self, *, server_id: str, companion: str,
                 mcp: McpClient, persona: str | None = None) -> None:
        self.server_id = server_id
        self.companion = companion
        self.persona = persona
        self.mcp = mcp
        listing = mcp.call("list_companions", {})
        self.companion_id = resolve_companion_id(listing, companion)
        self.listing_text = listing if isinstance(listing, str) else json.dumps(listing)

        paths = build_paths(ROOT, server_id, self.companion_id)
        self.store = BrainSessionStore(ROOT / "sessions", server_id, self.companion_id)
        self.memory = WorldMemoryStore(
            ROOT / "memory" / server_id / f"{self.companion_id}.json")
        self.inbox = EventInbox(ROOT / "inbox" / server_id / f"{self.companion_id}.jsonl")
        self.lock_path = paths["lock"]

        live_tools = mcp.tools()
        tools, requires_control = model_tools(live_tools)
        local_tools = make_world_memory_tools(self.memory)
        local_tools["load_skill"] = make_load_skill(ROOT / "skills")
        local_schemas = [
            {"name": "load_skill", "description": "加载 skills/<name>.md 技能内容",
             "parameters": {"type": "object", "properties": {
                 "name": {"type": "string", "description": "技能名（不带 .md）"}},
                 "required": ["name"]}},
            {"name": "remember_block", "description": "记录一个重要方块的位置",
             "parameters": {"type": "object", "properties": {
                 "type": {"type": "string"}, "x": {"type": "integer"},
                 "y": {"type": "integer"}, "z": {"type": "integer"},
                 "note": {"type": "string"}},
                 "required": ["type", "x", "y", "z"]}},
            {"name": "recall_blocks", "description": "回忆记过的重要方块(按距离排序)",
             "parameters": {"type": "object", "properties": {
                 "x": {"type": "number"}, "z": {"type": "number"},
                 "type": {"type": "string"}, "limit": {"type": "integer"}}}},
        ]
        self.persona_text = load_persona(ROOT, persona) if persona else None
        self.runtime = ActionBrainRuntime(
            store=self.store,
            checkpoint=TurnCheckpoint(paths["checkpoint"]),
            mcp=mcp,
            companion=self.companion_id,
            model=create_model_adapter(live_transport, persona=self.persona_text),
            tools=[*tools, *local_schemas],
            requires_control=requires_control,
            local_tools=local_tools,
            # 复合生存任务常需十余步；真循环由专项 guard 快速中断。
            max_rounds=24,
            event_boundary=self._event_boundary,
        )
        self.lock = threading.Lock()
        self._boundary_seen: set[str] = set()

    def list_companions(self) -> str:
        return self.listing_text

    def list_recent_events(self, count: int = 50) -> list[dict[str, Any]]:
        result = self.mcp.call("get_recent_events",
                               {"companion": self.companion_id, "count": count})
        if isinstance(result, dict) and isinstance(result.get("events"), list):
            return result["events"]
        return []

    def get_self_status(self) -> dict[str, Any]:
        result = self.mcp.call("get_self_status", {"companion": self.companion_id})
        return result if isinstance(result, dict) else {}

    def _event_boundary(self) -> str:
        from active_agent import fingerprint, _guidance_from_events
        # 模型等待时每两秒泵一次；派发前仍会核 generation，服务端兜住竞态。
        import time
        now = time.monotonic()
        if now - getattr(self, "_last_event_check", -10) < 2:
            return ""
        self._last_event_check = now
        status = self.get_self_status()
        generation = status.get("action_generation")
        if generation is not None and getattr(self, "_action_generation", generation) != generation:
            raise TurnCancelled("服务端取消围栏已变化")
        if generation is not None:
            self.runtime.action_generation = generation
        events = list(reversed(self.list_recent_events(count=50)))
        fresh = [e for e in events if fingerprint(e) not in self._boundary_seen]
        targeted = [e for e in fresh if str(e.get("detail", "")).startswith("@" + self.companion + " ")]
        if any(e.get("kind") == "cancel" for e in targeted):
            # 不消费事件；外层 Agent 持久处理 cancel 与其后的新聊天。
            raise TurnCancelled("玩家已取消当前回合")
        guidance = [e for e in targeted if e.get("kind") == "chat"]
        for e in guidance:
            self.inbox.push(e)
        self._boundary_seen.update(fingerprint(e) for e in fresh)
        self._consumed_event_fingerprints = set(self._boundary_seen)
        return _guidance_from_events(guidance)

    def _refresh_locked(self) -> None:
        for store in (self.store, getattr(self, "memory", None)):
            if hasattr(store, "reload"):
                store.reload()
        if hasattr(self, "mcp"):
            status = self.get_self_status()
            self._action_generation = status.get("action_generation")
            self.runtime.action_generation = self._action_generation
            self._last_event_check = -10
            from active_agent import fingerprint
            self._boundary_seen = {fingerprint(e) for e in self.list_recent_events(count=50)}
            self._consumed_event_fingerprints = set(self._boundary_seen)

    def run_turn(self, message: str) -> dict[str, Any]:
        with self.lock:
            try:
                with CompanionRunLock(self.lock_path):
                    self._refresh_locked()
                    if self.store.should_compact(max_messages=COMPACT_MAX_MESSAGES,
                                                 max_chars=COMPACT_MAX_CHARS):
                        compact_history(self.store, create_compactor(live_transport))
                    before = len(self.store.messages())
                    result = self.runtime.run_turn(
                        enrich_task_message(attach_events(message, self.inbox.read())))
                    self.inbox.clear()
                    return {"final": result["final"], "actions": result["actions"],
                            "history_before": before, "history_after": len(self.store.messages())}
            except BlockingIOError:
                # 同伴正被另一个大脑驱动（常驻 active agent / 其他 WebUI 会话）。
                # 返回友好提示，而不是 500 空回复——让前端能直接展示原因。
                return {"final": "该同伴正在被后台自动大脑驱动（例如名字提及自动回话），"
                                 "请稍候几秒再试，或先停止 numen-active-agent 服务。",
                        "actions": [], "busy": True}

    def recover_turn(self, guidance: str | None = None) -> dict[str, Any]:
        with self.lock:
            with CompanionRunLock(self.lock_path):
                self._refresh_locked()
                before = len(self.store.messages())
                result = self.runtime.recover_turn(guidance=guidance)
                return {**result, "final": result.get("final", ""), "actions": result.get("actions", []),
                        "history_before": before, "history_after": len(self.store.messages())}

    def history(self) -> list[dict[str, str]]:
        return self.store.messages()

    def memory_blocks(self) -> list[dict[str, Any]]:
        return self.memory.recall(limit=100)

    def events(self) -> list[dict[str, Any]]:
        return self.inbox.read()


def make_handler(context_factory: Callable[[str, str | None, str | None], CompanionContext],
                 token: str, static_dir: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _auth(self) -> bool:
            # Bearer-only，避免 URL/history/referrer 泄漏；空 token 仅允许回环启动。
            if not token:
                return True
            header = self.headers.get("Authorization", "")
            return hmac.compare_digest(header, "Bearer " + token)

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self) -> None:
            # 静态页面本身放行（前端有 token 输入框 / URL 参数直达）：
            # 只有 /v1/* API 和 /health 强制鉴权。
            if self.path == "/" or self.path.startswith("/index.html"):
                self._serve_static()
                return
            if not self._auth():
                self._json(401, {"error": "unauthorized"})
                return
            if self.path == "/health":
                self._json(200, {"ok": True})
                return
            parsed = self._parse_path()
            if parsed is None:
                self._json(404, {"error": "not found"})
                return
            route, params, context = parsed
            if route == "companions":
                ctx = context_factory(params.get("server", "numen-ab"), None, None)
                self._json(200, {"companions": ctx.list_companions()})
            elif route in ("history", "memory", "events"):
                ctx = self._ctx_or_error(params)
                if ctx is None:
                    return
                if route == "history":
                    self._json(200, {"messages": ctx.history()})
                elif route == "memory":
                    self._json(200, {"blocks": ctx.memory_blocks()})
                else:
                    self._json(200, {"events": ctx.events()})
            elif route == "root":
                self._serve_static()
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._auth():
                self._json(401, {"error": "unauthorized"})
                return
            parsed = self._parse_path()
            if parsed is None:
                self._json(404, {"error": "not found"})
                return
            route, params, _ = parsed
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or length < 0:
                    self._json(400, {"error": "invalid request framing"})
                    return
                if length > 65536:
                    self._json(413, {"error": "request body too large"})
                    return
                body = self._read_body()
                if not isinstance(body, dict):
                    raise ValueError("body must be an object")
            except (ValueError, UnicodeDecodeError, TimeoutError):
                self._json(400, {"error": "invalid request body"})
                return
            if route == "turns":
                try:
                    ctx = context_factory(
                        body.get("server", "numen-ab"), body.get("companion"),
                        body.get("persona"))
                except (ValueError, RuntimeError) as exc:
                    # 同伴不存在/dormant/名字歧义 → 友好错误，不 500 空回复
                    self._json(400, {"error": str(exc),
                                     "hint": "若同伴刚死亡，等待它复活（live）后再操作；"
                                             "或用 list_companions 确认名字"})
                    return
                if ctx:
                    self._json(200, ctx.run_turn(str(body.get("message", ""))))
            elif route == "recover":
                ctx = context_factory(
                    body.get("server", "numen-ab"), body.get("companion"), None)
                if ctx:
                    self._json(200, ctx.recover_turn())
            else:
                self._json(404, {"error": "not found"})

        def _ctx_or_error(self, params: dict[str, str]) -> CompanionContext | None:
            try:
                return context_factory(
                    params.get("server", "numen-ab"),
                    params.get("companion"),
                    params.get("persona"),
                )
            except (ValueError, RuntimeError) as exc:
                self._json(400, {"error": str(exc)})
                return None

        def _parse_path(self) -> tuple[str, dict[str, str], CompanionContext] | None:
            # 根路径允许带查询参数（?token=xxx 直达），按不带参数的根处理
            if self.path.split("?", 1)[0] == "/":
                return ("root", {}, None)
            match = re.fullmatch(r"/v1/([a-z]+)(?:\?([^#]*))?", self.path)
            if not match:
                return None
            route = match.group(1)
            params = dict(re.findall(r"([^&=]+)=([^&]*)", match.group(2) or ""))
            return (route, params, None)

        def _serve_static(self) -> None:
            index = static_dir / "index.html"
            if not index.exists():
                self._json(404, {"error": "webui missing"})
                return
            body = index.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:  # 静默访问日志
            pass

    return Handler


class BoundedHTTPServer(ThreadingHTTPServer):
    """最多八个请求线程，忙时拒绝；socket 超时由 Handler 限制。"""
    daemon_threads = True
    def __init__(self, *args):
        self._slots = threading.BoundedSemaphore(8)
        super().__init__(*args)
    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise
    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class BrainServer:
    """带认证的常驻 HTTP 服务；port=0 时使用随机端口（测试用）。"""

    def __init__(self, *, host: str, port: int, token: str,
                 factory: Callable[[str, str | None, str | None], CompanionContext],
                 static_dir: Path) -> None:
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise ValueError("BrainServer only supports loopback; use an authenticated tunnel")
        handler = make_handler(factory, token, static_dir)
        self._httpd = BoundedHTTPServer((host, port), handler)

    def start(self) -> None:
        thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def bound_port(self) -> int:
        return self._httpd.server_address[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18777)
    parser.add_argument("--token", default="", help="Bearer token；为空时仅限本机且无认证")
    parser.add_argument("--static-dir", default=str(ROOT / "webui"))
    args = parser.parse_args()

    def factory(server_id: str, companion: str | None,
                persona: str | None) -> CompanionContext:
        mcp = McpClient()
        mcp.initialize()
        if companion is None:
            return _ListingContext(server_id, mcp.call("list_companions", {}))
        return CompanionContext(server_id=server_id, companion=companion,
                                mcp=mcp, persona=persona)

    server = BrainServer(host=args.host, port=args.port, token=args.token,
                         factory=factory, static_dir=Path(args.static_dir))
    server.start()
    url = f"http://{args.host}:{server.bound_port()}"
    direct = url + ("/#token=" + urllib.parse.quote(args.token) if args.token else "")
    print(json.dumps({
        "status": "up",
        "url": url,
        "direct_url": direct,
        "auth": bool(args.token),
        "hint": "浏览器直接打开 direct_url 即可（已带 token，无需输入）",
    }, ensure_ascii=False, indent=2))
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()


class _ListingContext:
    """仅用于 /v1/companions 的轻量上下文：返回同伴列表文本。"""

    def __init__(self, server_id: str, listing: Any) -> None:
        self._listing = listing if isinstance(listing, str) else json.dumps(listing)

    def list_companions(self) -> str:
        return self._listing


if __name__ == "__main__":
    main()
