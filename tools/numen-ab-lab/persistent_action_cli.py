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
只使用 <available_tools> 中列出的工具。

【工具契约 · 务必遵守】
- 树、矿石、矿物是【方块】，不是实体！scan_nearby_entities 只能看到生物/玩家/动物，
  永远找不到树和矿。需要木材/矿石时【直接调用 mine】，它自带扫描+寻路+挖掘+计数，
  一次调用直到凑够 count 个物品；把同族变体都放进 block_ids（如 oak_log 与
  spruce_log、iron_ore 与 deepslate_iron_ore）。不要先 scan 找树再挖——直接 mine。
- 感知世界：look_around 同步返回周围 ASCII 地形；scan_nearby_entities 同步列出
  怪物/动物/玩家；get_self_status/get_world_info 查身体与世界。
- scan_blocks / locate_biome / locate_structure 是延迟查询：调用后会返回 qN task_id，
  运行时会自动轮询到 done 并把真实结构化结果交给你。它们不占身体动作车道。
  资源采集仍应直接用 mine，不要为了普通挖矿先做大范围 scan。
- 移动：goto 带 x+z 到目的地；要走到某方块旁用 goto(block=方块id)。
- 打怪：scan_nearby_entities 拿怪物 entity_id → melee_attack(entity_ids=[...])。
- 长任务（砍树/挖矿/盖房等）会跨多轮：每轮调用一个工具，拿到结果后继续下一步，
  直到目标完成才 final。复合任务先准备后续阶段的关键前置：若目标包含熔炼/加工，
  先确保工作台（或原木/木板）、炉子（或8圆石）和燃料，再深入采集目标资源，
  避免拿到矿后才从深处远途返回补加工设施。
  中途遇到意外（被怪打、掉血、天黑）要优先应对：先保命
  （逃跑/进食/反击），再继续任务，最后在 final 里说明发生了什么。
- 用户要求采集、制作或熔炼的最终产物保留在背包。除非用户明确要求丢下、交付或存入容器，
  不得对最终成果调用 drop_items；完成后用 get_self_status 核验数量并直接 final。
- 玩家点你名给任务 → 执行；纯聊天问候 → 简短回应即可。"""


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
            "max_tokens": 512,
            "reasoning_effort": "none",
            "messages": [
                {"role": "system", "content": persona_block + SYSTEM_PROMPT
                 + "\n<available_tools>" + tool_text + "</available_tools>"},
                *messages,
            ],
        }
        response = transport(payload)
        message = response["choices"][0]["message"]
        # 只信任模型最终输出 content。推理模型(qwen3.5-9b 思维链)的思考在
        # reasoning_content，不能拿去当决策——那会导致"吐字截断/拿思考顶包"。
        # content 为空表示本轮没产出决策文本(推理占满预算/只输出思考)。
        # 此时追加一条纠正消息重试一次(明确要求只给 JSON)，而不是拿思考顶包
        # 或直接失败。仍为空才抛错，交由外层兜底。
        content = message.get("content") or ""
        if not isinstance(content, str) or not content.strip():
            correction = [{
                "role": "assistant", "content": "(无输出)",
            }, {
                "role": "user",
                "content": "你没有产出任何决策文本，只输出了思考过程。现在直接给出 "
                           "规定的 JSON 决策对象(不含 Markdown、不含任何解释)。",
            }]
            payload = dict(payload, messages=[*payload["messages"], *correction])
            response = transport(payload)
            message = response["choices"][0]["message"]
            content = message.get("content") or ""
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


def enrich_task_message(message: str) -> str:
    """为需要后续加工的请求注入强制前置，避免弱模型先深入采集再补设施。"""
    smelting_terms = ("熔炼", "熔成", "烧成", "烧制", "冶炼", "smelt")
    if not any(term in message.lower() for term in smelting_terms):
        return message
    return """<mandatory_prerequisites>
这是“采集后熔炼/加工”任务。禁止先采目标矿。首次资源动作前必须先确认并准备：
1. 工作台，或制作工作台所需的原木/木板；
2. 炉子，或制作炉子所需的8个圆石；
3. 足够燃料（煤/木炭/可燃木料）。
只有上述加工链已在背包或可达位置落实，才开始采目标矿。没有合格近战武器时不要 melee_attack，先逃离。
最终产物必须保留在背包；除非用户明确要求交付，否则不得 drop_items 最终成果。完成后用 get_self_status 核验数量并 final。
</mandatory_prerequisites>

""" + message


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
    # 单请求 60s 超时 + 失败重试 1 次 + 间隔 1s，避免单轮推理把守护进程卡住
    # 25 分钟（旧值 150s×2=300s），让轮询/事件响应保持灵敏。
    return retry_transient(
        lambda: post_json(LLM_URL, payload, headers=LLM_HEADERS, timeout=60),
        attempts=2,
        delay_seconds=1,
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
            "max_tokens": 512,
            "reasoning_effort": "none",
            "messages": [{"role": "user", "content": COMPACT_PROMPT + "\n\n" + history_text}],
        }
        response = transport(payload)
        message = response["choices"][0]["message"]
        # 同样只信 content；压缩是纯摘要，不需要推理文本。
        content = message.get("content") or ""
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
        # 对有实质进展的长链任务宽容；死循环由专项 guard 限制。
        max_rounds=24,
    )

    before = len(store.messages())
    inbox = EventInbox(ROOT / "inbox" / args.server_id / f"{companion_id}.jsonl")
    with CompanionRunLock(paths["lock"]):
        if not args.recover and store.should_compact(
                max_messages=COMPACT_MAX_MESSAGES, max_chars=COMPACT_MAX_CHARS):
            compact_history(store, create_compactor(live_transport))
        if not args.recover:
            user_message = attach_events(enrich_task_message(args.message), inbox.read())
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
