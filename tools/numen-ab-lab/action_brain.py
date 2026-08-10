#!/usr/bin/env python3
"""每同伴持久会话与 Numen MCP 身体之间的最小行动运行时。"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from brain_runner import parse_decision
from persistent_brain import BrainSessionStore, fsync_dir

TERMINAL_STATES = {"done", "failed", "timeout", "stopped"}


def compact_tool_outcome(name: str, outcome: dict[str, Any]) -> dict[str, Any]:
    """长期历史只保留派发、task_id 与终态；高频轮询样本留在运行审计结果中。"""
    return {
        "name": name,
        "dispatch": outcome.get("dispatch"),
        "task_id": outcome.get("task_id"),
        "terminal": outcome.get("terminal"),
    }


class RecoveryRequired(RuntimeError):
    """上次进程可能已经产生游戏副作用，禁止静默重放。"""


class CompanionRunLock:
    """每个 UUID 的非阻塞进程锁，防止两个长期大脑并发写同一会话。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None

    def __enter__(self) -> "CompanionRunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise BlockingIOError(f"companion brain is already running: {self.path}") from exc
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(str(os.getpid()))
        self._handle.flush()
        return self

    def __exit__(self, _kind: Any, _value: Any, _traceback: Any) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class TurnCheckpoint:
    """原子写入单个未提交行动回合的恢复信息。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)
        fsync_dir(self.path.parent)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def resolve_companion_id(listing: Any, companion: str) -> str:
    """从实时列表中按完整名字绑定唯一 UUID，拒绝模糊或缺失身份。"""
    text = listing if isinstance(listing, str) else json.dumps(listing, ensure_ascii=False)
    pattern = re.compile(
        rf"(?m)^{re.escape(companion)}\s+id=([0-9a-fA-F-]{{36}})\s+\[live\]\s*$"
    )
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one live companion named {companion}, got {len(matches)}")
    return matches[0].lower()


def _task_id(value: Any) -> str | None:
    if isinstance(value, dict):
        direct = value.get("task_id")
        if isinstance(direct, str) and direct:
            return direct
        for nested in value.values():
            found = _task_id(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _task_id(nested)
            if found:
                return found
    return None


def execute_tool_exact(mcp: Any, *, companion: str, name: str,
                       arguments: dict[str, Any], requires_control: bool,
                       sleep: Callable[[float], None] = time.sleep,
                       timeout_seconds: float = 120.0,
                       on_dispatched: Callable[[Any, str | None], None] | None = None,
                       client_action_id: str | None = None) -> dict[str, Any]:
    """短租约派发工具；异步任务只按原 task_id 等待明确终态。

    写动作默认注入 client_action_id：服务端按该 ID 幂等派发，即使回执丢失，
    重发同一 ID 也不会产生第二次副作用。
    """
    call_args = dict(arguments)
    call_args["companion"] = companion
    if requires_control:
        if client_action_id is None:
            client_action_id = str(uuid.uuid4())
        call_args["client_action_id"] = client_action_id
        lease_id = mcp.call("acquire_control", {"companion": companion})["lease_id"]
        call_args["lease_id"] = lease_id
    else:
        lease_id = None
    try:
        dispatch = mcp.call(name, call_args)
    finally:
        pass
    task_id = _task_id(dispatch)
    if on_dispatched is not None:
        on_dispatched(dispatch, task_id)
    # 派发回执(含 task_id)已优先落盘；释放失败不能掩盖已取得的动作结果，
    # 租约有服务端 TTL 兜底(LeaseRegistry 会清扫)。
    if lease_id is not None:
        try:
            mcp.call("release_control", {"companion": companion, "lease_id": lease_id})
        except Exception:
            pass
    if task_id is None:
        return {"dispatch": dispatch, "task_id": None, "terminal": None, "samples": []}

    samples: list[Any] = []
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = mcp.call("task_status", {"companion": companion, "task_id": task_id})
        samples.append(status)
        state = status.get("state") if isinstance(status, dict) else None
        if state in TERMINAL_STATES:
            return {"dispatch": dispatch, "task_id": task_id,
                    "terminal": status, "samples": samples}
        if state == "idle":
            raise RuntimeError(f"task_status returned idle for exact task_id {task_id}")
        sleep(1.0)
    raise TimeoutError(f"task {task_id} did not reach a retained terminal state")


class ActionBrainRuntime:
    """把一个用户请求执行为多轮模型决策，并在最终完成时整批提交历史。"""

    def __init__(self, *, store: BrainSessionStore, checkpoint: TurnCheckpoint,
                 mcp: Any, companion: str, model: Any,
                 tools: list[dict[str, Any]], requires_control: dict[str, bool],
                 sleep: Callable[[float], None] = time.sleep, max_rounds: int = 8,
                 max_format_retries: int = 2,
                 local_tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None) -> None:
        self.store = store
        self.checkpoint = checkpoint
        self.mcp = mcp
        self.companion = companion
        self.model = model
        self.tools = tools
        self.requires_control = requires_control
        self.sleep = sleep
        self.max_rounds = max_rounds
        self.max_format_retries = max_format_retries
        # 本地工具不经 MCP、不持租约(如 load_skill / remember_block)，由运行时直接执行。
        self.local_tools = local_tools or {}

    def run_turn(self, user_content: str) -> dict[str, Any]:
        existing = self.checkpoint.read()
        if existing is not None:
            raise RecoveryRequired(
                f"incomplete side-effect checkpoint exists at {self.checkpoint.path}: "
                f"{existing.get('state', 'unknown')}"
            )

        turn_id = str(uuid.uuid4())
        base = self.store.messages()
        transient: list[dict[str, str]] = [{"role": "user", "content": user_content}]
        side_effect_started = False
        try:
            return self._continue_turn(
                base=base, transient=transient, actions=[], turn_id=turn_id,
                user_content=user_content, start_round=1,
                mark_side_effect=lambda: None,
            )
        except BaseException:
            current = self.checkpoint.read()
            side_effect_started = current is not None
            if not side_effect_started:
                self.checkpoint.clear()
            raise

    def _commit(self, turn_id: str, transcript: list[dict[str, str]]) -> None:
        self.checkpoint.write({
            "state": "committing", "turn_id": turn_id, "transcript": transcript,
        })
        self.store.append_batch(transcript, transaction_id=turn_id)
        self.checkpoint.clear()

    def _continue_turn(self, *, base: list[dict[str, str]],
                       transient: list[dict[str, str]], actions: list[dict[str, Any]],
                       turn_id: str, user_content: str, start_round: int,
                       mark_side_effect: Callable[[], None]) -> dict[str, Any]:
        for number in range(start_round, self.max_rounds + 1):
                correction: list[dict[str, str]] = []
                decision = None
                for attempt in range(self.max_format_retries + 1):
                    raw = self.model([*base, *transient, *correction], self.tools)
                    try:
                        decision = parse_decision(raw)
                        break
                    except Exception as exc:
                        if attempt >= self.max_format_retries:
                            raise ValueError(
                                f"model did not return a valid JSON decision after "
                                f"{self.max_format_retries + 1} attempts"
                            ) from exc
                        correction.extend([
                            {"role": "assistant", "content": raw},
                            {"role": "user", "content":
                             "格式错误。只返回规定的一个 JSON 对象，不要 Markdown 或额外文字。"},
                        ])
                assert decision is not None
                if decision["type"] == "final":
                    final = decision["content"]
                    transcript = [*transient, {"role": "assistant", "content": final}]
                    self._commit(turn_id, transcript)
                    return {"final": final, "actions": actions, "rounds": number}

                name = decision["name"]
                available = name in self.requires_control or name in self.local_tools
                if not available:
                    raise ValueError(f"tool not available: {name}")
                serialized_decision = json.dumps(decision, ensure_ascii=False, sort_keys=True)
                transient.append({"role": "assistant", "content": serialized_decision})
                if name in self.local_tools:
                    # 本地工具：同步执行，无 MCP 回执与租约。
                    outcome = {
                        "dispatch": None, "task_id": None,
                        "terminal": self.local_tools[name](decision["arguments"]),
                        "samples": [],
                    }
                    actions.append({"tool": name, **outcome})
                    tool_content = json.dumps(
                        compact_tool_outcome(name, outcome), ensure_ascii=False, sort_keys=True
                    )
                    transient.append({
                        "role": "user",
                        "content": f'<tool_result name="{name}">{tool_content}</tool_result>',
                    })
                    continue
                client_action_id = str(uuid.uuid4())
                self.checkpoint.write({
                    "state": "tool_planned", "turn_id": turn_id, "user": user_content,
                    "tool": name, "arguments": decision["arguments"],
                    "client_action_id": client_action_id,
                    "transcript": transient,
                })
                mark_side_effect()
                def record_dispatch(dispatch: Any, task_id: str | None) -> None:
                    self.checkpoint.write({
                        "state": "tool_dispatched", "turn_id": turn_id,
                        "user": user_content,
                        "tool": name, "arguments": decision["arguments"],
                        "client_action_id": client_action_id,
                        "dispatch": dispatch, "task_id": task_id,
                        "transcript": transient,
                    })

                outcome = execute_tool_exact(
                    self.mcp, companion=self.companion, name=name,
                    arguments=decision["arguments"],
                    requires_control=self.requires_control.get(name, False), sleep=self.sleep,
                    on_dispatched=record_dispatch,
                    client_action_id=client_action_id,
                )
                actions.append({"tool": name, **outcome})
                tool_content = json.dumps(
                    compact_tool_outcome(name, outcome), ensure_ascii=False, sort_keys=True
                )
                transient.append({
                    "role": "user",
                    "content": f'<tool_result name="{name}">{tool_content}</tool_result>',
                })
                self.checkpoint.write({
                    "state": "tool_completed", "turn_id": turn_id,
                    "user": user_content,
                    "tool": name, "task_id": outcome["task_id"],
                    "terminal": outcome["terminal"], "transcript": transient,
                })
        raise RuntimeError(f"maximum rounds exceeded: {self.max_rounds}")

    def _poll_existing_task(self, task_id: str, timeout_seconds: float = 120.0) -> dict[str, Any]:
        samples: list[Any] = []
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self.mcp.call("task_status", {
                "companion": self.companion, "task_id": task_id,
            })
            samples.append(status)
            state = status.get("state") if isinstance(status, dict) else None
            if state in TERMINAL_STATES:
                return {"terminal": status, "samples": samples}
            if state == "idle":
                raise RuntimeError(f"task_status returned idle for exact task_id {task_id}")
            self.sleep(1.0)
        raise TimeoutError(f"task {task_id} did not reach a retained terminal state")

    def recover_turn(self) -> dict[str, Any]:
        checkpoint = self.checkpoint.read()
        if checkpoint is None:
            raise RecoveryRequired("no incomplete turn to recover")
        turn_id = checkpoint.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RecoveryRequired("checkpoint has no turn_id")
        if self.store.has_transaction(turn_id):
            self.checkpoint.clear()
            return {"state": "already_committed", "turn_id": turn_id}

        state = checkpoint.get("state")
        if state == "committing":
            transcript = checkpoint.get("transcript")
            if not isinstance(transcript, list):
                raise RecoveryRequired("committing checkpoint has no transcript")
            self.store.append_batch(transcript, transaction_id=turn_id)
            self.checkpoint.clear()
            return {"state": "committed_during_recovery", "turn_id": turn_id}
        if state == "tool_planned":
            # 有幂等键时可以安全重发：服务端对同一 client_action_id 只派发一次，
            # 即使上次已受理也只是拿回原 task_id，不会产生第二次副作用。
            client_action_id = checkpoint.get("client_action_id")
            if not isinstance(client_action_id, str) or not client_action_id:
                raise RecoveryRequired("ambiguous planned action has no dispatch receipt; refusing replay")
            tool_name = str(checkpoint.get("tool"))
            arguments = checkpoint.get("arguments")
            if not isinstance(arguments, dict):
                raise RecoveryRequired("planned action checkpoint has no arguments")
            transient = checkpoint.get("transcript")
            if not isinstance(transient, list):
                raise RecoveryRequired("checkpoint has no recoverable transcript")
            user_content = checkpoint.get("user")
            if not isinstance(user_content, str):
                raise RecoveryRequired("checkpoint has no user request")
            actions: list[dict[str, Any]] = []
            name = tool_name
            outcome = execute_tool_exact(
                self.mcp, companion=self.companion, name=name,
                arguments=arguments,
                requires_control=self.requires_control.get(name, False), sleep=self.sleep,
                client_action_id=client_action_id,
            )
            actions.append({"tool": name, **outcome})
            tool_content = json.dumps(
                compact_tool_outcome(name, outcome), ensure_ascii=False, sort_keys=True
            )
            transient.append({
                "role": "user",
                "content": f'<tool_result name="{name}">{tool_content}</tool_result>',
            })
            self.checkpoint.write({
                "state": "tool_completed", "turn_id": turn_id,
                "user": user_content, "tool": name, "task_id": outcome["task_id"],
                "client_action_id": client_action_id,
                "terminal": outcome["terminal"], "transcript": transient,
            })
            return self._continue_turn(
                base=self.store.messages(), transient=transient, actions=actions,
                turn_id=turn_id, user_content=user_content, start_round=1,
                mark_side_effect=lambda: None,
            )

        transient = checkpoint.get("transcript")
        if not isinstance(transient, list):
            raise RecoveryRequired("checkpoint has no recoverable transcript")
        user_content = checkpoint.get("user")
        if not isinstance(user_content, str):
            raise RecoveryRequired("checkpoint has no user request")
        actions: list[dict[str, Any]] = []

        if state == "tool_dispatched":
            task_id = checkpoint.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise RecoveryRequired("ambiguous dispatched action has no task_id")
            polled = self._poll_existing_task(task_id)
            outcome = {
                "dispatch": checkpoint.get("dispatch"), "task_id": task_id,
                "terminal": polled["terminal"], "samples": polled["samples"],
            }
            name = str(checkpoint.get("tool"))
            actions.append({"tool": name, **outcome})
            tool_content = json.dumps(
                compact_tool_outcome(name, outcome), ensure_ascii=False, sort_keys=True
            )
            transient.append({
                "role": "user",
                "content": f'<tool_result name="{name}">{tool_content}</tool_result>',
            })
            self.checkpoint.write({
                "state": "tool_completed", "turn_id": turn_id,
                "user": user_content, "tool": name, "task_id": task_id,
                "terminal": polled["terminal"], "transcript": transient,
            })
        elif state != "tool_completed":
            raise RecoveryRequired(f"unsupported checkpoint state: {state}")

        return self._continue_turn(
            base=self.store.messages(), transient=transient, actions=actions,
            turn_id=turn_id, user_content=user_content, start_round=1,
            mark_side_effect=lambda: None,
        )