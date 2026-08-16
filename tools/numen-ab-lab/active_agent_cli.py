#!/usr/bin/env python3
"""主动行为循环守护进程入口：轮询服务器事件，自动回应提及与处理生存状态。"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from active_agent import ActiveAgent, EventCursor
from brain_runner import McpClient
from brain_server import CompanionContext
from persistent_brain import safe_segment

ROOT = Path(__file__).resolve().parent


def make_cursor(server_id: str, companion_id: str) -> EventCursor:
    return EventCursor(ROOT / "cursors" / safe_segment(server_id)
                       / f"{safe_segment(companion_id)}.cursor.json")


def build_context_factory(server_id: str, persona: str | None = None) -> Any:
    def factory(companion: str) -> CompanionContext:
        mcp = McpClient()
        mcp.initialize()
        return CompanionContext(server_id=server_id, companion=companion,
                                mcp=mcp, persona=persona)
    return factory


def live_companions(mcp: McpClient) -> list[str]:
    """从 list_companions 文本解析 live 同伴名。"""
    listing = mcp.call("list_companions", {})
    text = listing if isinstance(listing, str) else json.dumps(listing)
    names = []
    for line in text.splitlines():
        if "[live]" in line:
            match = re.match(r"^(\S+)\s+id=", line.strip())
            if match:
                names.append(match.group(1))
    return names


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-id", default="numen-ab")
    parser.add_argument("--companions", default="",
                        help="逗号分隔同伴名；留空=所有 live 同伴")
    parser.add_argument("--persona")
    parser.add_argument("--poll", type=float, default=15.0, help="事件轮询间隔(秒)")
    parser.add_argument("--survival-interval", type=float, default=300.0,
                        help="生存检查间隔(秒)")
    parser.add_argument("--cooldown", type=float, default=120.0,
                        help="自动回合最小间隔(秒)")
    parser.add_argument("--mention-words", default="",
                        help="逗号分隔触发词；默认=同伴名")
    parser.add_argument("--once", action="store_true",
                        help="只跑一轮（测试/调试用），不进入常驻循环")
    args = parser.parse_args()

    factory = build_context_factory(args.server_id, args.persona)

    def companions() -> list[str]:
        if args.companions:
            return [c.strip() for c in args.companions.split(",") if c.strip()]
        mcp = McpClient()
        mcp.initialize()
        return live_companions(mcp)

    agent = ActiveAgent(
        context_factory=factory,
        cursor_store=lambda name, ctx: make_cursor(args.server_id, ctx.companion_id),
        poll_seconds=args.poll,
        survival_interval=args.survival_interval,
        cooldown_seconds=args.cooldown,
        mention_words=[w.strip() for w in args.mention_words.split(",") if w.strip()] or None,
    )

    if args.once:
        for companion in companions():
            result = agent.run_once(companion)
            if result is not None:
                print(json.dumps({"companion": companion, "final": result["final"]},
                                 ensure_ascii=False))
        return

    print(json.dumps({"status": "active-agent up", "server": args.server_id,
                      "poll": args.poll, "cooldown": args.cooldown}, ensure_ascii=False))
    agent.run_forever(companions)


if __name__ == "__main__":
    main()
