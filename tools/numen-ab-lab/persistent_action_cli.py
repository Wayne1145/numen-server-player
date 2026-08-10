#!/usr/bin/env python3
"""每个 Numen 同伴的持久行动大脑命令行入口。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable

from action_brain import (
    ActionBrainRuntime,
    CompanionRunLock,
    TurnCheckpoint,
    resolve_companion_id,
)
from event_inbox import EventInbox
from world_memory import WorldMemoryStore
from brain_runner import (
    LLM_URL,
    LLM_MODEL,
    LLM_HEADERS,
    McpClient,
    ensure_llm_ready,
    model_tools,
    post_json,
    retry_transient,
)
from persistent_brain import BrainSessionStore, compact_history, safe_segment

ROOT = Path(__file__).resolve().parent
COMPACT_MAX_MESSAGES = int(os.environ.get("NUMEN_COMPACT_MAX_MESSAGES", "60"))
COMPACT_MAX_CHARS = int(os.environ.get("NUMEN_COMPACT_MAX_CHARS", "18000"))
COMPACT_PROMPT = """请把下面的历史对话压缩成简洁的中文要点摘要，必须保留：
识别词/口令、任务目标与完成状态、已到达或使用过的位置、同伴身份信息、重要结论和未完成事项。
不要编造新事实；只输出摘要正文，不要任何前后缀。"""
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


def create_model_adapter(transport: Callable[[dict[str, Any]], dict[str, Any]],
                         persona: str | None = None) -> Callable:
    """把 OpenAI-compatible 传输适配为 ActionBrainRuntime 的决策函数。"""
    persona_block = (persona + "\n\n") if persona else ""
    def model(messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> str:
        tool_text = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "model": LLM_MODEL,
            "stream": False,
            "temperature": 0,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": persona_block + SYSTEM_PROMPT
                 + "\n<available_tools>" + tool_text + "</available_tools>"},
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


def load_persona(root: Path, name: str) -> str | None:
    """从 personas/<name>.md 加载角色卡；不存在返回 None。"""
    path = root / "personas" / f"{name}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def make_load_skill(skills_root: Path) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """本地工具 load_skill：读取 skills/<name>.md 内容交给模型。"""
    def load_skill(arguments: dict[str, Any]) -> dict[str, Any]:
        name = str(arguments.get("name", "")).strip()
        path = skills_root / f"{name}.md"
        if not name or not path.exists():
            return {"success": False, "error": f"skill not found: {name}"}
        return {"success": True, "name": name, "content": path.read_text(encoding="utf-8")}
    return load_skill


def make_world_memory_tools(store: Any) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    """本地工具 remember_block / recall_blocks。"""
    def remember_block(arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            entry = store.remember(
                str(arguments.get("type", "")), int(arguments.get("x", 0)),
                int(arguments.get("y", 0)), int(arguments.get("z", 0)),
                note=arguments.get("note"),
            )
            return {"success": True, "entry": entry}
        except (ValueError, TypeError) as exc:
            return {"success": False, "error": str(exc)}

    def recall_blocks(arguments: dict[str, Any]) -> dict[str, Any]:
        x = arguments.get("x")
        z = arguments.get("z")
        blocks = store.recall(
            x=None if x is None else float(x),
            z=None if z is None else float(z),
            type=arguments.get("type"),
            limit=int(arguments.get("limit", 20)),
        )
        return {"success": True, "blocks": blocks}

    return {"remember_block": remember_block, "recall_blocks": recall_blocks}


def attach_events(message: str, events: list[dict[str, Any]]) -> str:
    """把未读服务器事件附加到用户请求前；无事件时原样返回。"""
    if not events:
        return message
    lines = ["<server_events>"]
    for event in events:
        lines.append(f"[{event.get('kind', 'event')}] {event.get('detail', '')}")
    lines.append("</server_events>")
    return "\n".join(lines) + "\n\n" + message


def live_transport(payload: dict[str, Any]) -> dict[str, Any]:
    return retry_transient(
        lambda: post_json(LLM_URL, payload, headers=LLM_HEADERS, timeout=150),
        attempts=2,
        delay_seconds=3,
    )


def create_compactor(transport: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable:
    """把历史压缩成摘要文本；用于上下文预算触发时的 compact_history。"""
    def compact_model(messages: list[dict[str, str]]) -> str:
        # 实测：deepseek-v4-flash 对 system+长消息列表 的压缩指令返回空 content，
        # 对单条 user 拼接消息有效——因此把历史拼进一条 user 消息。
        history_text = "\n".join(
            f"[{m.get('role', '?')}] {m.get('content', '')}" for m in messages)
        payload = {
            "model": LLM_MODEL,
            "stream": False,
            "temperature": 0,
            "max_tokens": 600,
            "messages": [{"role": "user", "content": COMPACT_PROMPT + "\n\n" + history_text}],
        }
        response = transport(payload)
        message = response["choices"][0]["message"]
        content = message.get("content") or message.get("reasoning_content") or ""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("compactor returned empty summary")
        return content
    return compact_model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-id", default="numen-ab")
    parser.add_argument("--companion", required=True)
    parser.add_argument("--message")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--persona", help="personas/<name>.md 中的角色卡名称")
    args = parser.parse_args()
    if not args.recover and not args.message:
        parser.error("--message is required unless --recover is used")

    mcp = McpClient()
    mcp.initialize()
    listing = mcp.call("list_companions", {})
    companion_id = resolve_companion_id(listing, args.companion)
    live_tools = mcp.tools()
    tools, requires_control = model_tools(live_tools)

    # 本地工具：技能加载 + 世界记忆；模型可见 schema 由运行时直接提供。
    memory_store = WorldMemoryStore(ROOT / "memory" / args.server_id / f"{companion_id}.json")
    local_tools = make_world_memory_tools(memory_store)
    local_tools["load_skill"] = make_load_skill(ROOT / "skills")
    local_schemas = [
        {"name": "load_skill", "description": "加载 skills/<name>.md 技能内容",
         "parameters": {"type": "object", "properties": {
             "name": {"type": "string", "description": "技能名（不带 .md）"}},
             "required": ["name"]}},
        {"name": "remember_block", "description": "记录一个重要方块的位置(工作台/箱子/熔炉等)",
         "parameters": {"type": "object", "properties": {
             "type": {"type": "string", "description": "方块 id 如 minecraft:crafting_table"},
             "x": {"type": "integer"}, "y": {"type": "integer"}, "z": {"type": "integer"},
             "note": {"type": "string", "description": "可选备注"}},
             "required": ["type", "x", "y", "z"]}},
        {"name": "recall_blocks", "description": "回忆记过的重要方块(按距离排序)",
         "parameters": {"type": "object", "properties": {
             "x": {"type": "number", "description": "当前 x，用于按距离排序"},
             "z": {"type": "number", "description": "当前 z，用于按距离排序"},
             "type": {"type": "string", "description": "可选方块 id 过滤"},
             "limit": {"type": "integer"}}}},
    ]
    tools = [*tools, *local_schemas]

    paths = build_paths(ROOT, args.server_id, companion_id)
    store = BrainSessionStore(ROOT / "sessions", args.server_id, companion_id)
    persona = load_persona(ROOT, args.persona) if args.persona else None
    runtime = ActionBrainRuntime(
        store=store,
        checkpoint=TurnCheckpoint(paths["checkpoint"]),
        mcp=mcp,
        companion=args.companion,
        model=create_model_adapter(live_transport, persona=persona),
        tools=tools,
        requires_control=requires_control,
        local_tools=local_tools,
        max_rounds=10,
    )

    before = len(store.messages())
    inbox = EventInbox(ROOT / "inbox" / args.server_id / f"{companion_id}.jsonl")
    with CompanionRunLock(paths["lock"]):
        if not args.recover and store.should_compact(
                max_messages=COMPACT_MAX_MESSAGES, max_chars=COMPACT_MAX_CHARS):
            compact_history(store, create_compactor(live_transport))
        if not args.recover:
            user_message = attach_events(args.message, inbox.read())
        else:
            user_message = args.message
        result = runtime.recover_turn() if args.recover else runtime.run_turn(user_message)
        if not args.recover:
            inbox.clear()
    output = {
        "server_id": args.server_id,
        "companion": args.companion,
        "companion_id": companion_id,
        "persona": args.persona,
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
