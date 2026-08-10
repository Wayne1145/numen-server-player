#!/usr/bin/env python3
"""同伴的世界记忆：记录/回忆重要方块位置（工作台、箱子、熔炉等）。"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from persistent_brain import fsync_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WorldMemoryStore:
    """按同伴隔离的 JSON 记忆文件，原子写入。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._blocks: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        blocks = data.get("blocks") if isinstance(data, dict) else None
        if isinstance(blocks, list):
            self._blocks = [b for b in blocks if isinstance(b, dict)]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"blocks": self._blocks}, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)
        fsync_dir(self.path.parent)

    def remember(self, block_type: str, x: int, y: int, z: int,
                 note: str | None = None) -> dict[str, Any]:
        """记录/更新一个位置；同一类型同一坐标视为同一目标，只更新备注。"""
        block_type = block_type.strip()
        if not block_type:
            raise ValueError("block_type is required")
        for existing in self._blocks:
            if (existing.get("type") == block_type
                    and existing.get("x") == int(x)
                    and existing.get("y") == int(y)
                    and existing.get("z") == int(z)):
                if note is not None:
                    existing["note"] = note
                existing["seen_at"] = _now()
                self._save()
                return existing
        entry = {
            "type": block_type,
            "x": int(x), "y": int(y), "z": int(z),
            "seen_at": _now(),
            "id": str(uuid.uuid4()),
        }
        if note is not None:
            entry["note"] = note
        self._blocks.append(entry)
        self._save()
        return entry

    def recall(self, x: float | None = None, z: float | None = None,
               type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """按距离（提供 x,z 时）或最近记忆排序返回；type 可选过滤。"""
        items = self._blocks
        if type is not None:
            items = [b for b in items if b.get("type") == type]
        if x is not None and z is not None:
            items = sorted(
                items,
                key=lambda b: (float(b.get("x", 0)) - float(x)) ** 2
                + (float(b.get("z", 0)) - float(z)) ** 2,
            )
        else:
            items = sorted(items, key=lambda b: b.get("seen_at", ""), reverse=True)
        return [dict(b) for b in items[:limit]]
