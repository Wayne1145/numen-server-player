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
            "你刚刚收到了上面的服务器事件。如果玩家提到你或需要回应，请简短自然地回应；"
            "如果是你的死亡，简单说明情况。不要移动，不要做多余动作。")
        self._last_turn: dict[str, float] = {}
        self._last_survival_check: dict[str, float] = {}

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
        triggered = bool(new_events) and any(trigger.should_trigger(e) for e in new_events)
        if triggered:
            if self._in_cooldown(companion, now):
                # 冷却中：不触发也不推进游标，下次轮询重试，避免事件被错过。
                return None
            result = self._auto_turn(ctx, companion, new_events, now)

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
                    result = self._auto_turn(
                        ctx, companion, [], now,
                        message="你的生命值或饥饿度偏低，请处理生存状态"
                                "（进食/治疗等），并简短说明。不要移动。")

        # 游标推进到最新一条（已触发或已判定无关），避免历史事件无限重看。
        if events:
            cursor_store.save(fingerprint(events[0]))
        return result

    def _auto_turn(self, ctx: Any, companion: str,
                   events: list[dict[str, Any]], now: float,
                   message: str | None = None) -> dict[str, Any]:
        message = message or self.message_template
        for event in events:
            ctx.inbox.push(event)
        result = ctx.run_turn(message)
        self._last_turn[companion] = now
        return result

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
