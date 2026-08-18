#!/usr/bin/env python3
"""Numen A/B 外部大脑运行器：同一模型、同一身体、同一任务，只切换提示词。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import socket
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from persistent_brain import fsync_dir

ROOT = Path(__file__).resolve().parent
MCP_URL = os.environ.get("NUMEN_MCP_URL", "http://127.0.0.1:25585/mcp")
MCP_TOKEN = os.environ.get("NUMEN_MCP_TOKEN", "numen-ab-local-token-2026")
# 直连 DeepSeek（不再经过本地 api_proxy：避免被注入 HA 家居工具与八千代人格）
LLM_URL = os.environ.get("NUMEN_LLM_URL", "https://api.deepseek.com/v1/chat/completions")
LLM_MODEL = os.environ.get("NUMEN_LLM_MODEL", "deepseek-v4-flash")
LLM_KEY = os.environ.get("NUMEN_LLM_KEY", "")
LLM_HEADERS = {"Authorization": f"Bearer {LLM_KEY}"}


def ensure_llm_ready() -> None:
    """任意 OpenAI 兼容端点都应当带 API Key；仅在真实发起模型请求前检查，
    避免模块导入即失败（单元测试不调用模型）。检查读 os.environ，
    与 systemd 等真实启动环境一致。"""
    key = os.environ.get("NUMEN_LLM_KEY", "")
    url = os.environ.get("NUMEN_LLM_URL", "")
    if not key and url:
        raise RuntimeError(
            "NUMEN_LLM_KEY 环境变量未设置：外部模型端点需要提供 API Key")
RCON_PORT = int(os.environ.get("NUMEN_RCON_PORT", "25586"))
RCON_PASSWORD = os.environ.get("NUMEN_RCON_PASSWORD", "numen-ab-rcon-2026")
MAX_ROUNDS = 8

MANAGEMENT_TOOLS = {
    "list_companions", "create_companion", "delete_companion",
    "acquire_control", "release_control", "task_status", "task_stop",
}

GENERIC_PROMPT = """You are an external AI controlling one Minecraft player body.
Use the available tools to complete the user's request. Be concise.
Each turn output exactly ONE raw JSON object, with no markdown and no extra text:
{"type":"tool","name":"tool_name","arguments":{...}}
or {"type":"final","content":"short result"}.
The runtime manages companion identity, control leases and background-task polling.
Never include companion or lease_id yourself. Only call tools from <available_tools>."""

NUMEN_PROMPT = """You are a Numen: a capable Minecraft companion with a real player body.
Act, do not merely narrate. A physical request means call tools until the goal is actually done.
Perceive before acting when needed. Never claim success from an idle task alone: verify the world
or your body with the matching perception tool. Failed results teach; change the next action based
on the reason instead of repeating unchanged. Keep one body on one job and finish with a brief report.
Each turn output exactly ONE raw JSON object, with no markdown and no extra text:
{"type":"tool","name":"tool_name","arguments":{...}}
or {"type":"final","content":"short result"}.
The runtime manages companion identity, control leases and background-task polling.
Never include companion or lease_id yourself. Only call tools from <available_tools>."""

NUMEN_V2_PROMPT = """You are a Numen: a capable Minecraft companion with a real player body.
Act instead of narrating, then verify the result with an appropriate perception tool.
Minecraft navigation intentionally stops near a coordinate, not at its exact decimal centre.
For coordinate movement, a verified horizontal distance of 1.5 blocks or less means ARRIVED.
Once verification satisfies the request, output final immediately. Never repeat the same goto merely
to reduce a sub-block coordinate remainder. Failed results teach; change strategy instead of repeating.
Each turn output exactly ONE raw JSON object, with no markdown and no extra text:
{"type":"tool","name":"tool_name","arguments":{...}}
or {"type":"final","content":"short result"}.
The runtime manages companion identity, control leases and background-task polling.
Never include companion or lease_id yourself. Only call tools from <available_tools>."""


def inject_control_args(arguments: dict[str, Any], *, companion: str,
                        lease_id: str, requires_control: bool) -> dict[str, Any]:
    """在模型参数外层注入运行器持有的身体身份与写租约。"""
    out = dict(arguments)
    out["companion"] = companion
    if requires_control:
        out["lease_id"] = lease_id
    return out


def execute_tool_with_ephemeral_lease(mcp: Any, *, companion: str, name: str,
                                      arguments: dict[str, Any],
                                      requires_control: bool) -> tuple[Any, str | None]:
    """只在一次写动作调用期间持有租约，避免模型思考时间消耗 TTL。"""
    lease_id: str | None = None
    if requires_control:
        lease = mcp.call("acquire_control", {"companion": companion})
        lease_id = lease["lease_id"]
    call_args = inject_control_args(
        arguments, companion=companion, lease_id=lease_id or "",
        requires_control=requires_control,
    )
    try:
        result = mcp.call(name, call_args)
    finally:
        pass
    # 释放失败不能掩盖已取得的动作结果；租约有服务端 TTL 兜底。
    if lease_id is not None:
        try:
            mcp.call("release_control", {"companion": companion, "lease_id": lease_id})
        except Exception:
            pass
    return result, lease_id


def score_move_trace(trace: dict[str, Any], *, max_distance: float) -> dict[str, Any]:
    """移动任务只有调用 goto、主动复核状态且到达目标附近才算通过。"""
    calls = trace.get("tool_calls", [])
    missing = [name for name in ("goto", "get_self_status") if name not in calls]
    status = trace.get("final_status") or {}
    pos = status.get("position") if isinstance(status, dict) else None
    target = trace.get("target") or {}
    distance = math.inf
    if isinstance(pos, dict) and all(k in pos for k in ("x", "z")):
        distance = math.hypot(float(pos["x"]) - float(target["x"]),
                              float(pos["z"]) - float(target["z"]))
    return {
        "passed": not missing and distance <= max_distance,
        "missing_calls": missing,
        "distance_to_target": distance,
    }


def parse_decision(text: str) -> dict[str, Any]:
    """解析模型返回的单个 JSON 决策，并拒绝未定义类型。"""
    raw = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        raw = fence.group(1)
    decision = json.loads(raw)
    kind = decision.get("type")
    if kind == "tool":
        if not isinstance(decision.get("name"), str):
            raise ValueError("tool decision requires name")
        if not isinstance(decision.get("arguments"), dict):
            raise ValueError("tool decision requires arguments")
        return decision
    if kind == "final":
        if not isinstance(decision.get("content"), str):
            raise ValueError("final decision requires content")
        return decision
    raise ValueError(f"unknown decision type: {kind}")


def save_checkpoint(path: Path, trace: dict[str, Any]) -> None:
    """先写临时文件再替换，确保中断时不会留下半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    fsync_dir(path.parent)


def retry_transient(operation: Any, *, attempts: int = 2,
                    delay_seconds: float = 2.0) -> Any:
    """仅重试模型传输层的 502/503/504 或超时，不重放已执行的游戏动作。"""
    last: BaseException | None = None
    for index in range(attempts):
        try:
            return operation()
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in {502, 503, 504}:
                raise
            last = exc
            if index + 1 < attempts:
                time.sleep(delay_seconds)
    assert last is not None
    raise last


def post_json(url: str, body: dict[str, Any], headers: dict[str, str] | None = None,
              timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    merged = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=merged)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class McpClient:
    def __init__(self) -> None:
        self.seq = 0
        self.headers = {"Authorization": f"Bearer {MCP_TOKEN}"}

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.seq += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self.seq, "method": method}
        if params is not None:
            body["params"] = params
        result = post_json(MCP_URL, body, self.headers, timeout=30)
        if "error" in result:
            raise RuntimeError(f"MCP {method} failed: {result['error']}")
        return result.get("result", {})

    def initialize(self) -> None:
        self.rpc("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "numen-ab-brain", "version": "1"},
        })

    def tools(self) -> list[dict[str, Any]]:
        return self.rpc("tools/list").get("tools", [])

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        text = content[0].get("text", "") if content else ""
        if result.get("isError") or content and content[0].get("isError"):
            raise RuntimeError(f"MCP tool call failed: {text[:500]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


def rcon_packet(request_id: int, kind: int, text: str) -> bytes:
    body = struct.pack("<ii", request_id, kind) + text.encode("utf-8") + b"\0\0"
    return struct.pack("<i", len(body)) + body


def rcon_receive(sock: socket.socket) -> tuple[int, int, str]:
    header = sock.recv(4)
    if len(header) != 4:
        raise RuntimeError("RCON response header incomplete")
    size = struct.unpack("<i", header)[0]
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise RuntimeError("RCON response ended early")
        data += chunk
    request_id, kind = struct.unpack("<ii", data[:8])
    return request_id, kind, data[8:-2].decode("utf-8", "replace")


def rcon(command: str) -> str:
    with socket.create_connection(("127.0.0.1", RCON_PORT), timeout=10) as sock:
        sock.sendall(rcon_packet(1, 3, RCON_PASSWORD))
        request_id, _, _ = rcon_receive(sock)
        if request_id == -1:
            raise RuntimeError("RCON authentication failed")
        sock.sendall(rcon_packet(2, 2, command))
        return rcon_receive(sock)[2]


def clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """从模型可见 Schema 中移除由运行器注入的 companion/lease_id。"""
    out = json.loads(json.dumps(schema))
    props = out.get("properties", {})
    props.pop("companion", None)
    props.pop("lease_id", None)
    if isinstance(out.get("required"), list):
        out["required"] = [v for v in out["required"] if v not in {"companion", "lease_id"}]
    return out


def tool_requires_control(schema: dict[str, Any]) -> bool:
    """服务端工具只要暴露 lease_id 属性，就由运行器注入写租约。"""
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    return isinstance(properties, dict) and "lease_id" in properties


def model_tools(live_tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    # 正式服断链工具：这些是异步工具，但 actuator 同步路径不等待回调，
    # 永远返回 "action queued" 占位包且无 task_id（结果被丢弃）。模型拿不到
    # 任何反馈，只会空转。因此从模型可见列表剔除，避免被误导调用。
    # 替代品全部同步可用：look_around（视野）、scan_nearby_entities（实体）、
    # mine/goto/melee_attack（带 task_id 可等待）。后续 mod 修复后在此恢复。
    BROKEN_TOOLS = {"scan_blocks", "locate_biome", "locate_structure"}
    visible: list[dict[str, Any]] = []
    control: dict[str, bool] = {}
    seen: set[str] = set()
    for tool in live_tools:
        name = tool["name"]
        if name in MANAGEMENT_TOOLS:
            continue
        if name in BROKEN_TOOLS:
            continue
        if name in seen:
            raise ValueError(f"duplicate tool name from server: {name}")
        seen.add(name)
        schema = tool.get("inputSchema", {})
        control[name] = tool_requires_control(schema)
        visible.append({
            "name": name,
            "description": tool.get("description", ""),
            "parameters": clean_schema(schema),
        })
    return visible, control


def llm_turn(messages: list[dict[str, str]], system_prompt: str,
             tools: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    ensure_llm_ready()
    tool_text = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
    full_system = system_prompt + "\n<available_tools>" + tool_text + "</available_tools>"

    def request_once() -> dict[str, Any]:
        return post_json(LLM_URL, {
            "model": LLM_MODEL, "stream": False, "temperature": 0,
            "max_tokens": 2000, "reasoning_effort": "none",
            "messages": [{"role": "system", "content": full_system}, *messages],
        }, headers=LLM_HEADERS, timeout=120)

    response = retry_transient(request_once, attempts=2, delay_seconds=3)
    choice = response["choices"][0]["message"]
    return choice.get("content") or choice.get("reasoning_content") or "", response.get("usage", {})


def poll_idle(mcp: McpClient, companion: str, timeout_seconds: int = 90) -> list[Any]:
    samples: list[Any] = []
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = mcp.call("task_status", {"companion": companion})
        samples.append(status)
        if isinstance(status, dict) and status.get("state") == "idle":
            return samples
        time.sleep(1)
    raise TimeoutError(f"task did not return idle: {samples[-3:]}")


def prepare_companion(mcp: McpClient, companion: str) -> dict[str, Any]:
    """重建同伴并把它放到固定平坦场景，保证两个 Profile 起点相同。"""
    listed = mcp.call("list_companions", {})
    if companion.lower() in json.dumps(listed, ensure_ascii=False).lower():
        mcp.call("delete_companion", {"companion": companion})
        time.sleep(1)
    mcp.call("create_companion", {"name": companion})
    time.sleep(2)
    commands = [
        "fill -16 99 -16 16 110 16 minecraft:air",
        "fill -16 99 -16 16 99 16 minecraft:stone",
        f"tp {companion} 0.5 100 0.5",
        f"clear {companion}",
        f"effect clear {companion}",
        f"gamemode survival {companion}",
    ]
    results = [rcon(command) for command in commands]
    time.sleep(1)
    status = mcp.call("get_self_status", {"companion": companion})
    return {"commands": commands, "results": results, "status": status}


def run_case(profile: str) -> dict[str, Any]:
    config = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
    case = config["cases"][0]
    companion_by_profile = {
        "generic": "ABGeneric",
        "numen": "ABNumen",
        "numen-v2": "ABNumenV2",
    }
    prompt_by_profile = {
        "generic": GENERIC_PROMPT,
        "numen": NUMEN_PROMPT,
        "numen-v2": NUMEN_V2_PROMPT,
    }
    companion = companion_by_profile[profile]
    prompt = prompt_by_profile[profile]
    mcp = McpClient()
    mcp.initialize()
    live_tools = mcp.tools()
    tools, requires_control = model_tools(live_tools)
    scene = prepare_companion(mcp, companion)
    start = scene["status"]["position"]
    target = {
        "x": float(start["x"]) + float(case["reset"]["relative_target"]["dx"]),
        "z": float(start["z"]) + float(case["reset"]["relative_target"]["dz"]),
    }
    request = case["request"] + f"\n精确目标坐标：x={target['x']}, z={target['z']}。"
    messages: list[dict[str, str]] = [{"role": "user", "content": request}]
    rounds: list[dict[str, Any]] = []
    calls: list[str] = []
    final_status: dict[str, Any] | None = None
    started = time.monotonic()
    final_text = ""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = ROOT / "results" / f"{stamp}-{profile}-{case['id']}.json"

    def checkpoint(state: str, error: str | None = None) -> None:
        partial: dict[str, Any] = {
            "version": 2,
            "state": state,
            "profile": profile,
            "model": "deepseek-v4-flash (direct)",
            "case_id": case["id"],
            "companion": companion,
            "elapsed_seconds": time.monotonic() - started,
            "scene": scene,
            "target": target,
            "tool_calls": calls,
            "rounds": rounds,
            "final_text": final_text,
            "final_status": final_status,
        }
        if error is not None:
            partial["error"] = error
        save_checkpoint(out_path, partial)

    checkpoint("incomplete")
    try:
        for number in range(1, MAX_ROUNDS + 1):
            raw, usage = llm_turn(messages, prompt, tools)
            row: dict[str, Any] = {"round": number, "model_raw": raw, "usage": usage}
            try:
                decision = parse_decision(raw)
            except Exception as exc:
                row["parse_error"] = str(exc)
                rounds.append(row)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "格式错误。只返回规定的一个 JSON 对象。"})
                checkpoint("incomplete")
                continue
            row["decision"] = decision
            rounds.append(row)
            messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
            if decision["type"] == "final":
                final_text = decision["content"]
                checkpoint("incomplete")
                break
            name = decision["name"]
            if name not in requires_control:
                result: Any = {"success": False, "error": f"tool not available: {name}"}
            else:
                result, used_lease = execute_tool_with_ephemeral_lease(
                    mcp, companion=companion, name=name,
                    arguments=decision["arguments"],
                    requires_control=requires_control[name],
                )
                row["ephemeral_lease"] = used_lease
                calls.append(name)
                if name == "get_self_status" and isinstance(result, dict):
                    final_status = result
                if isinstance(result, dict) and "task_id" in json.dumps(result):
                    row["task_poll"] = poll_idle(mcp, companion)
            row["tool_result"] = result
            messages.append({
                "role": "user",
                "content": "<tool_result name=\"" + name + "\">"
                           + json.dumps(result, ensure_ascii=False) + "</tool_result>",
            })
            checkpoint("incomplete")
    except BaseException as exc:
        checkpoint("incomplete", f"{type(exc).__name__}: {exc}")
        raise

    trace: dict[str, Any] = {
        "version": 2,
        "state": "completed" if final_text else "max_rounds",
        "profile": profile,
        "model": "deepseek-v4-flash (direct)",
        "case_id": case["id"],
        "companion": companion,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "elapsed_seconds": time.monotonic() - started,
        "scene": scene,
        "target": target,
        "tool_calls": calls,
        "rounds": rounds,
        "final_text": final_text,
        "final_status": final_status,
    }
    trace["score"] = score_move_trace(
        trace, max_distance=float(case["pass"]["horizontal_distance_to_target_max"]),
    )
    save_checkpoint(out_path, trace)
    trace["result_path"] = str(out_path)
    return trace


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("generic", "numen", "numen-v2"), required=True)
    args = parser.parse_args()
    result = run_case(args.profile)
    print(json.dumps({
        "profile": result["profile"], "score": result["score"],
        "tool_calls": result["tool_calls"], "elapsed_seconds": result["elapsed_seconds"],
        "result_path": result["result_path"], "final_text": result["final_text"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
