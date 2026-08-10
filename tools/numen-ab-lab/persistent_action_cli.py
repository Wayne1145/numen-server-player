#!/usr/bin/env python3
"""每个 Numen 同伴的持久行动大脑命令行入口。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from action_brain import (
    ActionBrainRuntime,
    CompanionRunLock,
    TurnCheckpoint,
    resolve_companion_id,
)
from brain_runner import (
    LLM_URL,
    McpClient,
    model_tools,
    post_json,
    retry_transient,
)
from persistent_brain import BrainSessionStore, safe_segment

ROOT = Path(__file__).resolve().parent
SYSTEM_PROMPT = """你是一个与 Minecraft Numen 真实玩家身体长期绑定的同伴大脑。
你的历史按服务器与同伴 UUID 独立持久化。请使用历史维持身份、记忆和任务连续性。
需要改变游戏世界或身体时，实际调用工具；不要只叙述。每轮只输出一个原始 JSON 对象：
{"type":"tool","name":"工具名","arguments":{...}}
或 {"type":"final","content":"简短中文答复"}。
运行时会注入 companion 和短期控制租约。异步动作返回 task_id 后，运行时会按该 ID 等到
明确的 done/failed/timeout/stopped，并把结构化结果交还给你。不得把 idle 当作成功。
坐标移动在复核后水平距离不超过 1.5 格即可视为到达，不要为小数残差重复 goto。
工具失败时根据结构化原因改变策略；确认目标完成后立即 final。
只使用 <available_tools> 中列出的工具。"""


def build_paths(root: Path, server_id: str, companion_id: str) -> dict[str, Path]:
    server = safe_segment(server_id)
    companion = safe_segment(companion_id)
    return {
        "session": root / "sessions" / server / f"{companion}.jsonl",
        "checkpoint": root / "checkpoints" / server / f"{companion}.json",
        "lock": root / "locks" / server / f"{companion}.lock",
    }


def create_model_adapter(transport: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable:
    """把 OpenAI-compatible 传输适配为 ActionBrainRuntime 的决策函数。"""
    def model(messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> str:
        tool_text = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "model": "cloud",
            "stream": False,
            "temperature": 0,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + "\n<available_tools>"
                 + tool_text + "</available_tools>"},
                *messages,
            ],
        }
        response = transport(payload)
        message = response["choices"][0]["message"]
        content = message.get("content") or message.get("reasoning_content") or ""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("model returned empty decision")
        return content
    return model


def live_transport(payload: dict[str, Any]) -> dict[str, Any]:
    return retry_transient(
        lambda: post_json(LLM_URL, payload, timeout=150),
        attempts=2,
        delay_seconds=3,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-id", default="numen-ab")
    parser.add_argument("--companion", required=True)
    parser.add_argument("--message")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    if not args.recover and not args.message:
        parser.error("--message is required unless --recover is used")

    mcp = McpClient()
    mcp.initialize()
    listing = mcp.call("list_companions", {})
    companion_id = resolve_companion_id(listing, args.companion)
    live_tools = mcp.tools()
    tools, requires_control = model_tools(live_tools)
    paths = build_paths(ROOT, args.server_id, companion_id)
    store = BrainSessionStore(ROOT / "sessions", args.server_id, companion_id)
    runtime = ActionBrainRuntime(
        store=store,
        checkpoint=TurnCheckpoint(paths["checkpoint"]),
        mcp=mcp,
        companion=args.companion,
        model=create_model_adapter(live_transport),
        tools=tools,
        requires_control=requires_control,
        max_rounds=10,
    )

    before = len(store.messages())
    with CompanionRunLock(paths["lock"]):
        result = runtime.recover_turn() if args.recover else runtime.run_turn(args.message)
    output = {
        "server_id": args.server_id,
        "companion": args.companion,
        "companion_id": companion_id,
        "history_before": before,
        "history_after": len(store.messages()),
        "session_path": str(paths["session"]),
        "checkpoint_path": str(paths["checkpoint"]),
        "lock_path": str(paths["lock"]),
        "result": result,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
