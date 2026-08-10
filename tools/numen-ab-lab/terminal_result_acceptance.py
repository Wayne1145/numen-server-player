#!/usr/bin/env python3
"""在隔离 Numen 服务器上验收按 task_id 保留的异步任务终态。"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

from brain_runner import McpClient, prepare_companion

COMPANION = "ABTermResult"
TERMINAL_STATES = {"done", "failed", "timeout", "stopped"}
OUT = Path(__file__).resolve().parent / "results" / "terminal-result-live-acceptance.json"


def find_task_id(value: Any) -> str | None:
    """从动作响应中递归提取服务端返回的任务 ID。"""
    if isinstance(value, dict):
        for key in ("task_id", "taskId", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for nested in value.values():
            found = find_task_id(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = find_task_id(nested)
            if found:
                return found
    return None


def acquire(mcp: McpClient) -> str:
    lease = mcp.call("acquire_control", {"companion": COMPANION})
    return str(lease["lease_id"])


def release(mcp: McpClient, lease_id: str) -> Any:
    return mcp.call("release_control", {"companion": COMPANION, "lease_id": lease_id})


def dispatch_goto(mcp: McpClient, x: float, z: float) -> tuple[Any, str]:
    lease_id = acquire(mcp)
    try:
        response = mcp.call("goto", {
            "companion": COMPANION,
            "lease_id": lease_id,
            "x": x,
            "y": None,
            "z": z,
        })
    finally:
        release(mcp, lease_id)
    task_id = find_task_id(response)
    if not task_id:
        raise AssertionError(f"goto 响应没有 task_id: {response!r}")
    return response, task_id


def exact_status(mcp: McpClient, task_id: str) -> Any:
    return mcp.call("task_status", {"companion": COMPANION, "task_id": task_id})


def wait_terminal(mcp: McpClient, task_id: str, timeout: float = 120.0) -> list[Any]:
    samples: list[Any] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = exact_status(mcp, task_id)
        samples.append(status)
        if isinstance(status, dict) and status.get("state") in TERMINAL_STATES:
            return samples
        time.sleep(0.25)
    raise TimeoutError(f"任务 {task_id} 未进入终态: {samples[-5:]}")


def main() -> None:
    mcp = McpClient()
    mcp.initialize()
    preparation = prepare_companion(mcp, COMPANION)

    # 成功路径：六格水平移动应稳定完成。
    success_dispatch, success_id = dispatch_goto(mcp, 6.5, 0.5)
    active_without_id = mcp.call("task_status", {"companion": COMPANION})
    success_samples = wait_terminal(mcp, success_id)
    success_terminal = success_samples[-1]
    success_requery = exact_status(mcp, success_id)

    if success_terminal.get("state") != "done":
        raise AssertionError(f"成功任务终态不是 done: {success_terminal!r}")
    if not isinstance(success_terminal.get("result"), dict):
        raise AssertionError(f"成功任务缺少结构化 result: {success_terminal!r}")
    if success_requery != success_terminal:
        raise AssertionError("成功任务终态不能稳定重复查询")

    # 停止路径：派发远距离移动后，立即按原任务 ID 停止。
    stop_dispatch, stop_id = dispatch_goto(mcp, 80.5, 0.5)
    before_stop = exact_status(mcp, stop_id)
    stop_lease = acquire(mcp)
    try:
        stop_response = mcp.call("task_stop", {
            "companion": COMPANION,
            "lease_id": stop_lease,
            "task_id": stop_id,
        })
    finally:
        release(mcp, stop_lease)
    stop_samples = wait_terminal(mcp, stop_id)
    stop_terminal = stop_samples[-1]
    stop_requery = exact_status(mcp, stop_id)

    if stop_terminal.get("state") != "stopped":
        raise AssertionError(f"停止任务终态不是 stopped: {stop_terminal!r}")
    if not isinstance(stop_terminal.get("result"), dict):
        raise AssertionError(f"停止任务缺少结构化 result: {stop_terminal!r}")
    if stop_requery != stop_terminal:
        raise AssertionError("停止任务终态不能稳定重复查询")

    # 失败路径：合法派发一个不存在的方块 ID，任务应在执行层产生结构化失败终态。
    fail_lease = acquire(mcp)
    try:
        fail_dispatch = mcp.call("goto", {
            "companion": COMPANION,
            "lease_id": fail_lease,
            "x": None,
            "y": None,
            "z": None,
            "block": "minecraft:definitely_missing_block",
        })
    finally:
        release(mcp, fail_lease)
    fail_id = find_task_id(fail_dispatch)
    if not fail_id:
        raise AssertionError(f"失败任务响应没有 task_id: {fail_dispatch!r}")
    fail_samples = wait_terminal(mcp, fail_id)
    fail_terminal = fail_samples[-1]
    fail_requery = exact_status(mcp, fail_id)

    if fail_terminal.get("state") != "failed":
        raise AssertionError(f"失败任务终态不是 failed: {fail_terminal!r}")
    if not isinstance(fail_terminal.get("result"), dict):
        raise AssertionError(f"失败任务缺少结构化 result: {fail_terminal!r}")
    if fail_requery != fail_terminal:
        raise AssertionError("失败任务终态不能稳定重复查询")

    final_status = mcp.call("get_self_status", {"companion": COMPANION})
    evidence = {
        "accepted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "companion": COMPANION,
        "preparation": preparation,
        "success": {
            "dispatch": success_dispatch,
            "task_id": success_id,
            "active_without_id": active_without_id,
            "samples": success_samples,
            "terminal": success_terminal,
            "requery": success_requery,
        },
        "stopped": {
            "dispatch": stop_dispatch,
            "task_id": stop_id,
            "before_stop": before_stop,
            "stop_response": stop_response,
            "samples": stop_samples,
            "terminal": stop_terminal,
            "requery": stop_requery,
        },
        "failed": {
            "dispatch": fail_dispatch,
            "task_id": fail_id,
            "samples": fail_samples,
            "terminal": fail_terminal,
            "requery": fail_requery,
        },
        "final_status": final_status,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "evidence": str(OUT),
        "success_task_id": success_id,
        "success_terminal": success_terminal,
        "stopped_task_id": stop_id,
        "stopped_terminal": stop_terminal,
        "failed_task_id": fail_id,
        "failed_terminal": fail_terminal,
        "active_without_id": active_without_id,
        "final_position": final_status.get("position"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
