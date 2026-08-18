#!/usr/bin/env python3
"""主动行为循环：常驻守护进程，自动回应提及、处理死亡与生存状态。

机制：
- 每 poll_seconds 轮询 MCP get_recent_events；
- 事件游标（指纹文件）记录已处理到哪一条，重启不重复；
- 只有"值得回应"的事件触发回合（名字提及 / 自己死亡），并受全局冷却限制；
- 定期（survival_interval）检查生命/饥饿，偏低时触发生存处理回合；
- 触发时把新事件写入 inbox，由 CompanionContext.run_turn 附加进上下文并消费；
- 游标在处理完成后推进到最新一条（无论是否触发），避免历史事件无限重看。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from event_inbox import EventInbox
from persistent_brain import fsync_dir
from action_brain import RecoveryRequired


def fingerprint(event: dict[str, Any]) -> str:
    """事件的稳定指纹：字段排序序列化，用于游标比较。"""
    keys = ("kind", "source", "detail", "time")
    return json.dumps(
        {k: event.get(k) for k in keys}, sort_keys=True, ensure_ascii=False)


class EventCursor:
    """记录最后处理的事件指纹；原子写入，重启不丢。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            value = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def save(self, value: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)
        fsync_dir(self.path.parent)


class TriggerPolicy:
    """判定事件是否值得打断模型触发自动回合。"""

    def __init__(self, companion_name: str,
                 mention_words: list[str] | None = None) -> None:
        self.companion_name = companion_name
        self.mention_words = list(mention_words or [companion_name])

    def should_trigger(self, event: dict[str, Any]) -> bool:
        kind = event.get("kind")
        source = event.get("source", "")
        detail = event.get("detail", "") or ""
        if kind == "death" and source == self.companion_name:
            return True
        if kind == "chat":
            return any(word in detail for word in self.mention_words)
        return False


class SurvivalPolicy:
    """定期生存检查：生命或饥饿低于阈值时需要处理。"""

    def __init__(self, hp_threshold: float = 10.0,
                 hunger_threshold: int = 6) -> None:
        self.hp_threshold = hp_threshold
        self.hunger_threshold = hunger_threshold

    def needs_attention(self, status: dict[str, Any]) -> bool:
        hp = float(status.get("hp", 20.0))
        hunger = int(status.get("hunger", 20))
        return hp < self.hp_threshold or hunger < self.hunger_threshold


class ActiveAgent:
    """每轮驱动一个同伴：拉事件、判触发、必要时 run_turn。"""

    def __init__(self, *, context_factory: Callable[[str], Any],
                 cursor_store: Callable[[str], EventCursor] | EventCursor,
                 poll_seconds: float = 15.0,
                 survival_interval: float = 300.0,
                 cooldown_seconds: float = 120.0,
                 mention_words: list[str] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 survival_policy: SurvivalPolicy | None = None,
                 trigger_policy_factory: Callable[[str], TriggerPolicy] | None = None,
                 message_template: str | None = None) -> None:
        self.context_factory = context_factory
        self.cursor_store = cursor_store
        self.poll_seconds = poll_seconds
        self.survival_interval = survival_interval
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self.survival_policy = survival_policy or SurvivalPolicy()
        self.trigger_policy_factory = trigger_policy_factory or TriggerPolicy
        self.message_template = message_template or (
            "你刚刚收到了上面的服务器事件。这是玩家在游戏聊天里点你的名（@名字）。\n"
            "如果玩家给了具体任务（移动/挖掘/合成/放置/调查等），请按正常回合执行它——"
            "调用工具直到完成或安全地说明阻塞；如果是闲聊或问候，简短自然地回应即可。\n"
            "如果是你的死亡事件，简单说明情况，先恢复生存状态。\n"
            "规则：玩家点名给任务 → 执行；无任务纯聊天 → 回话。不要擅自做玩家没要求的大范围破坏。")
        self._last_turn: dict[str, float] = {}
        self._last_survival_check: dict[str, float] = {}
        self._recovery_failures: dict[str, int] = {}

    def _cursor_for(self, companion: str, ctx: Any) -> EventCursor:
        if callable(self.cursor_store):
            return self.cursor_store(companion, ctx)
        return self.cursor_store

    def _in_cooldown(self, companion: str, now: float) -> bool:
        last = self._last_turn.get(companion)
        return last is not None and (now - last) < self.cooldown_seconds

    def run_once(self, companion: str) -> dict[str, Any] | None:
        ctx = self.context_factory(companion)
        now = self.clock()
        trigger = self.trigger_policy_factory(companion)
        cursor_store = self._cursor_for(companion, ctx)

        # 挂起的回合 checkpoint（上次因超时/中断未完成）：优先尝试自动恢复，
        # 让长任务（mine/goto/build 等）在完成后能把结果接回来，而不是等到
        # 玩家再次点名才处理。恢复是幂等的（poll 原 task_id）。
        pending = getattr(getattr(ctx, "runtime", None), "checkpoint", None)
        if pending is not None and pending.path.exists():
            try:
                result = ctx.recover_turn()
                if result is not None:
                    final = (result or {}).get("final") or ""
                    if isinstance(final, str) and final.strip():
                        self._send_reply(ctx, final)
                    self._last_turn[companion] = now
                    return result
            except Exception as exc:
                # 任务仍在运行或模型/提供商暂时失败时保留 checkpoint，但必须
                # 留下可见日志。旧代码裸 pass 让服务看似 active、身体 idle，
                # 实际每轮恢复都可能超时/耗尽轮次，用户与运维侧完全无证据。
                _log.warning("挂起 checkpoint 自动恢复暂未完成: %s", exc)

        events = ctx.list_recent_events(count=50)
        cursor = cursor_store.load()
        new_events: list[dict[str, Any]] = []
        if cursor is not None:
            # events 最新在前；从最新往旧找游标，游标之前（更新）的事件才是新的。
            for event in events:
                if fingerprint(event) == cursor:
                    break
                new_events.append(event)
            new_events.reverse()
        elif events:
            # 首次运行：只建立游标（最新一条），不触发历史事件。
            cursor_store.save(fingerprint(events[0]))
            return None

        result = None
        consumed = False  # 本批事件已处理（触发执行 或 判定无关），应推进游标；cooldown 跳过不算
        triggered = bool(new_events) and any(trigger.should_trigger(e) for e in new_events)
        try:
            if triggered:
                if self._in_cooldown(companion, now):
                    # 冷却中：不触发也不推进游标，下次轮询重试，避免事件被错过。
                    return None
                consumed = True
                result = self._auto_turn(ctx, companion, new_events, now)
            elif new_events:
                # 有新事件但判定无关（不点名）：标记已消费，推进游标，
                # 避免同一批无关事件每轮都被重看。
                consumed = True

            # 定期生存检查（独立于事件触发）。
            last_check = self._last_survival_check.get(companion)
            if last_check is None or (now - last_check) >= self.survival_interval:
                self._last_survival_check[companion] = now
                if result is None and not self._in_cooldown(companion, now):
                    try:
                        status = ctx.get_self_status()
                    except Exception:
                        status = {}
                    if self.survival_policy.needs_attention(status):
                        consumed = True
                        result = self._auto_turn(
                            ctx, companion, [], now,
                            message="你的生命值或饥饿度偏低，请处理生存状态"
                                    "（进食/治疗等），并简短说明。不要移动。")
        finally:
            # 无论回合成功/失败都推进游标并清空收件箱：防止同一批事件在
            # _auto_turn 抛异常（如恢复失败）后无限重放，造成"恢复失败→
            # 清 checkpoint→再触发"死循环。事件已尝试处理过一次，即使失败
            # 也标记消费；玩家重新点名会带来新事件。
            # cooldown 跳过的轮次不推进（事件留给下次重试）。
            if consumed and events:
                cursor_store.save(fingerprint(events[0]))
            if consumed:
                try:
                    ctx.inbox.clear()
                except Exception:
                    pass
        return result

    def _auto_turn(self, ctx: Any, companion: str,
                   events: list[dict[str, Any]], now: float,
                   message: str | None = None) -> dict[str, Any]:
        message = message or self.message_template
        for event in events:
            ctx.inbox.push(event)
        try:
            result = ctx.run_turn(message)
        except RecoveryRequired:
            # 上次回合被外部中断（/numen stop、超时或进程被杀）而残留 checkpoint。
            # 自动尝试 recover_turn：恢复是幂等的（poll 原 task / 幂等
            # client_action_id），不会产生二次副作用；成功后正常走完回合并回话，
            # 避免"长任务被终止后假人永久沉默"。
            import logging
            _log = logging.getLogger("active_agent")
            # 用 checkpoint 文件路径作键，跟踪连续恢复失败次数，防止"坏 checkpoint
            # 反复恢复失败"死循环（如模型生成无效工具参数，重试不会变好）。
            ckpt_path = getattr(
                getattr(ctx, "runtime", None), "checkpoint", None)
            key = str(getattr(ckpt_path, "path", companion))
            try:
                _log.info("检测到残留 checkpoint，尝试自动恢复中断回合")
                result = ctx.recover_turn()
                self._recovery_failures.pop(key, None)
                _log.info("中断回合恢复完成: %s",
                          (result or {}).get("final", "")[:80])
            except Exception as recover_exc:
                n = self._recovery_failures.get(key, 0) + 1
                self._recovery_failures[key] = n
                _log.warning("自动恢复失败(%d/3): %s", n, recover_exc)
                if n >= 3:
                    # 连续 3 次失败说明 checkpoint 已坏（无效参数/损坏），
                    # 保留只会让该假人永久卡住；清除它，让新任务能启动。
                    _log.warning("恢复反复失败，清除损坏 checkpoint: %s", key)
                    if ckpt_path is not None:
                        try:
                            ckpt_path.clear()
                        except Exception:
                            pass
                    self._recovery_failures.pop(key, None)
                    # 放弃本轮恢复，转为普通回合（可能仍会抛，但至少不循环）。
                    raise
                raise
        # 把模型回合的最终回复发回游戏聊天栏，让玩家能直接看到/听到假人回应。
        # run_turn 返回 {"final": ..., "actions": [...], ...};final 是模型生成的
        # 中文/自然语言回复(可能含工具执行的总结)。用 MCP send_chat 让假人在
        # 游戏里说出去。若 final 为空(纯工具回合、无文本回复)则不重复发。
        final = (result or {}).get("final") or ""
        if isinstance(final, str) and final.strip():
            self._send_reply(ctx, final)
        self._last_turn[companion] = now
        return result

    def _send_reply(self, ctx: Any, final: str) -> None:
        """把文本发回游戏聊天栏（供 _auto_turn 与挂起回合恢复复用）。"""
        try:
            # send_chat 是 SERVER_BODY_ACTION，需要控制 lease。
            # 先 acquire(返回 lease_id) → send_chat → release。
            lease = ctx.mcp.call("acquire_control", {"companion": ctx.companion})
            lease_id = (lease or {}).get("lease_id") if isinstance(lease, dict) else None
            try:
                ctx.mcp.call("send_chat", {
                    "companion": ctx.companion,
                    "lease_id": lease_id,
                    "text": final.strip()[:300],
                })
            finally:
                if lease_id:
                    try:
                        ctx.mcp.call("release_control", {
                            "companion": ctx.companion, "lease_id": lease_id})
                    except Exception:
                        pass
        except Exception as exc:
            # 发不出回话不致命——AI 回合仍已执行完，仅提示。
            import logging
            logging.getLogger("active_agent").warning(
                "send_chat 回写游戏失败: %s", exc)

    def run_forever(self, companions_provider: Callable[[], list[str]]) -> None:
        """常驻循环：轮询所有 live 同伴。"""
        while True:
            try:
                for companion in companions_provider():
                    try:
                        self.run_once(companion)
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(self.poll_seconds)
