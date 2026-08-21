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
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from event_inbox import EventInbox
from persistent_brain import fsync_dir
from action_brain import RecoveryRequired

_log = logging.getLogger("numen.active-agent")
_log.addHandler(logging.NullHandler())


def fingerprint(event: dict[str, Any]) -> str:
    """事件的稳定指纹：字段排序序列化，用于游标比较。

    游标比较仍用原始完整字段（含 time/source），保证重启后能准确定位到
    上次处理到的那条事件，避免漏处理。
    """
    keys = ("kind", "source", "detail", "time")
    return json.dumps(
        {k: event.get(k) for k in keys}, sort_keys=True, ensure_ascii=False)


def _dedupe_death_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把大量重复的死亡事件压缩成 1 条。

    玩家被连续杀死几十次时，每个死亡事件 time 都不同（游标无法自然去重），
    会把几十条 [death] yachiyo died 全灌进模型上下文，淹没玩家真正下达的
    任务指令，并诱导模型反复执行无效的“复活/恢复”回合。模型真正需要的信息
    只是“我死过、已复活”，细节交给 get_self_status 现场读取即可。

    规则：
    - death 事件保留 1 条（最新的），其余丢弃；
    - chat / 其它事件原样保留（任务指令绝不能被压缩）。
    游标推进不受影响（游标按原始事件推进），这里只做上下文注入前的压缩。
    """
    if not events:
        return events
    out: list[dict[str, Any]] = []
    last_death: dict[str, Any] | None = None
    death_lines: list[int] = []
    for idx, ev in enumerate(events):
        if ev.get("kind") == "death":
            death_lines.append(idx)
            last_death = ev  # 保留最新的死亡事件
        else:
            # 遇到非死亡事件，把前面攒的死亡事件作为 1 条 flush 出去
            if last_death is not None:
                out.append(last_death)
                last_death = None
            out.append(ev)
    if last_death is not None:
        out.append(last_death)
    return out


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
        if kind in ("chat", "cancel"):
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


def _guidance_from_events(events: list[dict[str, Any]]) -> str:
    """把一批玩家点名事件格式化为注入引导文本（保留原始玩家消息与来源）。"""
    lines = []
    for e in events:
        detail = str(e.get("detail", "")).strip()
        if not detail:
            continue
        # 去掉可能的 @名字 前缀，保留玩家原话
        text = re.sub(r"^@\S+\s*", "", detail)
        source = str(e.get("source", "")).strip()
        if source:
            lines.append(f"[{source}] {text.strip() or detail}")
        else:
            lines.append(text.strip() or detail)
    return "\n".join(lines) if lines else ""


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

        # 先读事件（比 checkpoint 优先）：玩家点名/新消息必须能打断或
        # 注入到进行中的回合，而不是被 checkpoint 恢复挡在门外。
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

        # 分类新事件：玩家点名（含聊天/引导）与 cancel 指令。
        mentions = [e for e in new_events
                    if trigger.should_trigger(e) and e.get("kind") != "cancel"]
        cancels = [e for e in new_events
                   if e.get("kind") == "cancel" and trigger.should_trigger(e)]

        pending = getattr(getattr(ctx, "runtime", None), "checkpoint", None)
        checkpoint_exists = pending is not None and pending.path.exists()

        # cancel 事件：玩家明确叫停 → 清 checkpoint（保留已提交历史），
        # 让身体任务停在服务端已停的状态，不再自动恢复旧回合。
        if cancels:
            _log.info("检测到 cancel 事件(%d 条)，清除 checkpoint，旧回合作废", len(cancels))
            if checkpoint_exists:
                try:
                    pending.clear()
                    checkpoint_exists = False
                except Exception as exc:
                    _log.warning("清除 checkpoint 失败: %s", exc)

        # 玩家点名（chat/引导）且存在 checkpoint：注入引导，不打断任务。
        if mentions and checkpoint_exists:
            try:
                guidance = _guidance_from_events(mentions)
                if guidance:
                    _log.info("任务进行中收到玩家点名(%d 条)，注入引导继续当前回合", len(mentions))
                    result = ctx.recover_turn(guidance=guidance)
                    if result is not None:
                        final = (result or {}).get("final") or ""
                        if isinstance(final, str) and final.strip():
                            self._send_reply(ctx, final)
                        self._last_turn[companion] = now
                        self._save_cursor(cursor_store, events, consumed=True)
                        self._clear_inbox(ctx, consumed=True)
                        return result
            except Exception as exc:
                _log.warning("注入引导恢复回合暂未完成: %s", exc)

        # 无玩家点名：挂起 checkpoint 优先自动恢复（原逻辑）。
        if checkpoint_exists:
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
            self._save_cursor(cursor_store, events, consumed)
            self._clear_inbox(ctx, consumed)
        return result

    def _auto_turn(self, ctx: Any, companion: str,
                   events: list[dict[str, Any]], now: float,
                   message: str | None = None) -> dict[str, Any]:
        message = message or self.message_template
        injected = _dedupe_death_events(events)
        for event in injected:
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

    @staticmethod
    def _save_cursor(cursor_store: Any, events: list[dict[str, Any]],
                     consumed: bool) -> None:
        """推进游标到最新事件（仅当本轮已消费事件）。"""
        if consumed and events:
            try:
                cursor_store.save(fingerprint(events[0]))
            except Exception:
                pass

    @staticmethod
    def _clear_inbox(ctx: Any, consumed: bool) -> None:
        """清空事件收件箱（仅当本轮已消费事件）。"""
        if consumed:
            try:
                ctx.inbox.clear()
            except Exception:
                pass

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
