#!/usr/bin/env python3
"""同伴事件收件箱：死亡/聊天等服务器事件暂存，供下一回合附加进上下文。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class EventInbox:
    """按同伴隔离的 JSONL 事件文件；回合成功提交后由调用方 clear()。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def push(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    def clear(self) -> None:
        """清空事件（保留空文件，避免目录抖动）。"""
        if self.path.exists():
            with self.path.open("w", encoding="utf-8") as handle:
                handle.write("")
                handle.flush()
                os.fsync(handle.fileno())
