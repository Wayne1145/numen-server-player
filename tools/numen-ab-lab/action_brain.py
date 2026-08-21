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

TERMINAL_STATES = {"done", "failed", "timeout", "stopped", "error"}


def _is_queued_empty(dispatch: Any) -> bool:
    """判定 MCP 工具回执是否为"排队占位包"(无任何实质数据)。

    同步感知工具(get_self_status / scan_nearby_entities / look_around /
    get_world_info 等)返回 dispatch 里有 hp/inventory/entities 等真实数据,
    只是没有 task_id——这些不是空结果。真正的黑洞是异步工具在同步路径
    下返回的占位包:{"success":true,"message":"action queued (sync task) —
    perceive to confirm"} 或空 dict,模型拿不到任何反馈。这里区分两者,
    避免 empty_result_cap 误伤正常同步工具。
    """
    if not isinstance(dispatch, dict):
        return not dispatch          # None/[] 视为空
    message = str(dispatch.get("message") or "")
    if "queued" in message.lower():
        return True                  # 排队占位包
    # message 本身就是实质结果：lookup_recipe 等同步工具把完整配方放在
    # message 中，不一定另带 data。旧逻辑排除了 message，导致连续两次
    # 成功配方查询被误计为两个空结果并触发 early_final。
    if message.strip():
        return False
    substantive = [k for k in dispatch if k not in ("success", "type")]
    return not substantive           # 空字典/仅 success、type → 空


def _is_business_tool_failure(exc: BaseException) -> bool:
    """区分可供模型修正的工具业务拒绝与基础设施异常。"""
    text = str(exc)
    normalized = text.replace(" ", "").lower()
    return "MCP tool call failed:" in text and (
        '"success":false' in normalized
        or "tool rejected the call:" in text.lower()
        or "身体正忙" in text
    )


def compact_tool_outcome(name: str, outcome: dict[str, Any]) -> dict[str, Any]:
    """长期历史只保留派发、task_id 与终态；高频轮询样本留在运行审计结果中。"""
    return {
        "name": name,
        "dispatch": outcome.get("dispatch"),
        "task_id": outcome.get("task_id"),
        "terminal": outcome.get("terminal"),
    }


def _same_decision(a: Any, b: Any) -> bool:
    """比较两条 assistant 决策是否相同（同工具 + 同参数）。

    用于检测模型陷入"反复派发同一异步任务"的循环：只有工具名和参数
    序列化后完全一致才算重复，避免误伤"同一工具不同目标"的合法调用。
    """
    try:
        if isinstance(a, str):
            a = json.loads(a)
        if isinstance(b, str):
            b = json.loads(b)
    except (ValueError, TypeError):
        return False
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    if a.get("type") != "tool" or b.get("type") != "tool":
        return False
    return a.get("name") == b.get("name") and a.get("arguments") == b.get("arguments")


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
                       timeout_seconds: float = 300.0,
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
    except BaseException:
        # 派发失败没有回执可落盘，但仍必须立刻释放租约；否则只能等
        # 服务端 TTL，连续业务失败会制造次生“控制权正忙”。
        if lease_id is not None:
            try:
                mcp.call("release_control", {
                    "companion": companion, "lease_id": lease_id,
                })
            except Exception:
                pass
        raise
    task_id = _task_id(dispatch)
    if on_dispatched is not None:
        # 成功派发时仍保持原有崩溃安全顺序：先把回执/task_id 落 checkpoint，
        # 再释放租约，避免“动作已受理但恢复信息未保存”的歧义窗口。
        on_dispatched(dispatch, task_id)
    if lease_id is not None:
        try:
            mcp.call("release_control", {
                "companion": companion, "lease_id": lease_id,
            })
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


def _guidance_message(guidance: str) -> str:
    """把玩家在任务进行中插入的引导包装成模型可见的 user 消息。

    语义：不打断原任务，仅追加指引让模型下一轮决策时读取。
    """
    return (
        f"<player_guidance>\n"
        f"玩家在任务进行中补了一句话（不是新任务，请不要丢弃当前任务，"
        f"先读完再决定是否需要调整计划）：\n"
        f"{guidance.strip()}\n"
        f"</player_guidance>"
    )


class ActionBrainRuntime:
    """把一个用户请求执行为多轮模型决策，并在最终完成时整批提交历史。"""

    def __init__(self, *, store: BrainSessionStore, checkpoint: TurnCheckpoint,
                 mcp: Any, companion: str, model: Any,
                 tools: list[dict[str, Any]], requires_control: dict[str, bool],
                 sleep: Callable[[float], None] = time.sleep, max_rounds: int = 8,
                 max_format_retries: int = 2,
                 local_tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
                 empty_result_cap: int = 2) -> None:
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
        self.empty_result_cap = empty_result_cap
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
                empty_result_cap=self.empty_result_cap,
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
                       mark_side_effect: Callable[[], None],
                       empty_result_cap: int = 2) -> dict[str, Any]:
        """驱动多轮决策直到模型收敛到 final 或轮次超限。

        空结果收敛保护：当连续 N 次工具调用都返回 terminal=None（无终态、
        无 task_id 的"queued"空包）时，主动打断循环并写一条 final，
        避免模型在"永远填不满的反馈黑洞"里无限重试。
        """
        consecutive_empty = 0
        unavailable_budget = 0
        pending_correction: list[dict[str, str]] = []
        for number in range(start_round, self.max_rounds + 1):
                # 跨轮保留循环/不可用工具纠正。旧代码把 correction 建在循环内，
                # append 后立刻 continue，下一轮又重置为空，模型从未见到提示。
                correction = pending_correction
                pending_correction = []
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

                # 循环检测：模型反复派发同一异步任务（同工具+同参数）时，
                # 不重复执行，而是注入提示引导它用 task_status 查进度或收敛到
                # final。否则会因 execute_tool_exact 同步等待超时/轮次超限而死循环。
                if decision["type"] == "tool":
                    # 只拦截“紧邻的原样重复”。同一工具在中间发生了采集、
                    # 合成、移动、放置等状态变化后必须允许重试：例如第一次
                    # craft iron_pickaxe 因无工作台失败，放好工作台后再次 craft
                    # 是合法推进。旧逻辑扫描整个 transient，导致这类重试被
                    # 永久拉黑并空耗 max_rounds。
                    latest_assistant = next((
                        m.get("content") for m in reversed(transient)
                        if m.get("role") == "assistant"
                    ), None)
                    is_immediate_duplicate = _same_decision(latest_assistant, decision)
                    if is_immediate_duplicate:
                        # 若前次同名工具执行返回的是真正 queued 空包，说明卡的是
                        # “排队黑洞”；走 empty_result_cap，不与重复保护互相打架。
                        last_empty = (
                            actions and actions[-1].get("tool") == decision["name"]
                            and actions[-1].get("terminal") is None
                            and actions[-1].get("task_id") is None
                            and _is_queued_empty(actions[-1].get("dispatch"))
                        )
                        if not last_empty:
                            pending_correction.append({
                                "role": "user",
                                "content": (
                                    "注意：你刚刚重复派发了同一个工具且参数完全相同。"
                                    "如果它返回了 task_id，说明是异步任务，请用 "
                                    "task_status 查询其进度（别重复派发同一任务）；"
                                    "如果它已完成或无法完成，请直接返回 final 总结结果。"
                                    "不要再派发重复的工具调用。"
                                ),
                            })
                            continue

                name = decision["name"]
                available = name in self.requires_control or name in self.local_tools
                if not available:
                    # 模型从历史会话学到的旧工具名（如 scan_blocks/locate_biome），
                    # 本服已从可见列表剔除（异步断链）。崩溃会毁掉整个回合，
                    # 正确做法是给模型一句可读的纠正反馈，让它改用替代工具继续。
                    # 连续不可用超过 3 次视为模型无法收敛，直接 final 收束。
                    if unavailable_budget is None:
                        unavailable_budget = 0
                    unavailable_budget += 1
                    pending_correction.append({
                        "role": "user",
                        "content": (
                            f"工具 {name} 在当前服务器不可用（已停用/不在可用列表）。"
                            "请改用以下可用替代：找方块/资源用 mine（自带寻路挖掘计数，"
                            "block_ids 传方块 id 列表）；看视野用 look_around；找实体用 "
                            "scan_nearby_entities；移动用 goto。不要重复调用不可用工具。"
                        ),
                    })
                    if unavailable_budget >= 3:
                        unavailable_final = (
                            f"已连续 {unavailable_budget} 次尝试调用不可用工具（含 {name}），"
                            "当前服务器工具集不包含它。为避免空转，我先停下汇报："
                            "请改用 mine / look_around / scan_nearby_entities 完成目标。"
                        )
                        transient.append({"role": "assistant", "content": unavailable_final})
                        return {"final": unavailable_final, "actions": actions,
                                "rounds": number, "early_final": "unavailable_tool_cap"}
                    continue
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

                try:
                    outcome = execute_tool_exact(
                        self.mcp, companion=self.companion, name=name,
                        arguments=decision["arguments"],
                        requires_control=self.requires_control.get(name, False), sleep=self.sleep,
                        on_dispatched=record_dispatch,
                        client_action_id=client_action_id,
                    )
                except RuntimeError as exc:
                    if not _is_business_tool_failure(exc):
                        raise
                    # 缺材料、参数拒绝等业务失败是规划反馈：同一回合立即
                    # 回灌模型，不必先崩溃、等下一次 poll 再恢复。
                    failure = {"success": False, "message": str(exc)}
                    outcome = {"dispatch": failure, "task_id": None,
                               "terminal": failure, "samples": []}
                actions.append({"tool": name, **outcome})
                # 空结果收敛保护：仅当工具返回"排队占位包"（无任何实质数据）时
                # 才计数。同步感知工具（get_self_status / scan_nearby_entities /
                # look_around）虽然 task_id=None，但 dispatch 里有真实结果，
                # 不算空——否则模型每连续调用两次同步工具就会被误判掐断，
                # 表现为"明明有结果却说感知不可用"。真正的黑洞是
                # {"success":true,"message":"action queued ..."} 这种占位包。
                terminal = outcome.get("terminal")
                if terminal is None and outcome.get("task_id") is None \
                        and _is_queued_empty(outcome.get("dispatch")):
                    consecutive_empty += 1
                    if consecutive_empty >= empty_result_cap:
                        empty_final = (
                            f"已连续 {consecutive_empty} 次调用工具，但工具都只返回"
                            "‘已排队/无结果’占位包，没有拿到可确认的结果。"
                            "为避免无限重试，我先停下并汇报：当前工具反馈链路不可用，"
                            "暂时无法确认或完成目标。建议改用可同步返回结果的工具。"
                        )
                        transcript = [*transient,
                                      {"role": "assistant", "content": empty_final}]
                        self._commit(turn_id, transcript)
                        return {"final": empty_final, "actions": actions,
                                "rounds": number, "early_final": "empty_result_cap"}
                else:
                    consecutive_empty = 0
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

    def _poll_existing_task(self, task_id: str, timeout_seconds: float = 300.0) -> dict[str, Any]:
        samples: list[Any] = []
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                status = self.mcp.call("task_status", {
                    "companion": self.companion, "task_id": task_id,
                })
            except RuntimeError as exc:
                # 任务完成/被停后服务端会清理记录,后续 task_status 返回
                # isError+unknown task_id(MCP 层抛 RuntimeError)。此时任务
                # 已经结束(结果已消费或丢失),把它当作终态收束,而不是
                # 一直重试到超时把回合挂死。
                msg = str(exc)
                if "unknown task_id" in msg:
                    return {"terminal": {
                        "state": "done", "task_id": task_id,
                        "detail": "cleared after completion", "result": None,
                    }, "samples": samples}
                raise
            samples.append(status)
            state = status.get("state") if isinstance(status, dict) else None
            if state in TERMINAL_STATES:
                return {"terminal": status, "samples": samples}
            if state == "idle":
                raise RuntimeError(f"task_status returned idle for exact task_id {task_id}")
            self.sleep(1.0)
        raise TimeoutError(f"task {task_id} did not reach a retained terminal state")

    def recover_turn(self, guidance: str | None = None) -> dict[str, Any]:
        """恢复中断回合；guidance 为玩家在任务进行中插入的新指令/指引。

        与“打断”不同：旧任务不取消，只是把玩家消息作为一条 user 消息
        追加进当前回合的 transient（旧工具结果之后），让模型下一轮决策
        时能看到并可能调整计划。历史（已提交 session）保留不变。
        """
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
            try:
                outcome = execute_tool_exact(
                    self.mcp, companion=self.companion, name=name,
                    arguments=arguments,
                    requires_control=self.requires_control.get(name, False), sleep=self.sleep,
                    client_action_id=client_action_id,
                )
                terminal = outcome.get("terminal")
            except RuntimeError as exc:
                if not _is_business_tool_failure(exc):
                    raise
                # 业务失败（如 craft 缺材料、mine 无目标）不是系统错误：把失败
                # 结果交还给模型重新决策，而不是抛异常让恢复计数堆积 →
                # 3 次后清 checkpoint 丢回合。模型看到"缺 2x stick"后会转而
                # 合成木棍/砍树，再回来合成铁镐——长线任务的关键迭代路径。
                failure = {"success": False, "message": str(exc)}
                outcome = {"dispatch": failure, "task_id": None,
                           "terminal": failure, "samples": []}
                terminal = failure
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
                "terminal": terminal, "transcript": transient,
            })
            if guidance:
                transient.append({
                    "role": "user",
                    "content": _guidance_message(guidance),
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

        if guidance:
            transient.append({
                "role": "user",
                "content": _guidance_message(guidance),
            })
        return self._continue_turn(
            base=self.store.messages(), transient=transient, actions=actions,
            turn_id=turn_id, user_content=user_content, start_round=1,
            mark_side_effect=lambda: None,
        )