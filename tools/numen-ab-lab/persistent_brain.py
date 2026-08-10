#!/usr/bin/env python3
"""每个 Minecraft 同伴独立的持久大脑会话。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

VALID_ROLES = {"user", "assistant", "tool"}


def fsync_dir(path: Path) -> None:
    """尽力而为地同步目录项；文件系统不支持时忽略(如部分 Windows/网络盘)。"""
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    return cleaned or "unknown"


def _reject_path_separators(*values: str) -> None:
    """身份键含路径分隔符时拒绝，避免 safe_segment 把 a/b 与 a_b 折叠成同一路径。"""
    for value in values:
        if "/" in value or "\\" in value:
            raise ValueError(f"identity contains a path separator: {value!r}")


class BrainSessionStore:
    """以 server_id + companion UUID 分区的追加式 JSONL 会话。"""

    def __init__(self, root: Path, server_id: str, companion_id: str) -> None:
        _reject_path_separators(server_id, companion_id)
        self.path = (root / safe_segment(server_id)
                     / f"{safe_segment(companion_id)}.jsonl")
        self._transactions: set[str] = set()
        self._messages = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        messages: list[dict[str, Any]] = []
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    break
                raise
            batch = record.get("messages") if isinstance(record, dict) else None
            transaction_id = record.get("transaction_id") if isinstance(record, dict) else None
            if isinstance(transaction_id, str) and transaction_id:
                self._transactions.add(transaction_id)
            records = batch if isinstance(batch, list) else [record]
            for message in records:
                if (not isinstance(message, dict) or message.get("role") not in VALID_ROLES
                        or not isinstance(message.get("content"), str)):
                    raise ValueError(f"invalid persisted message at line {index + 1}")
                messages.append({"role": message["role"], "content": message["content"]})
        return messages

    def append(self, role: str, content: str) -> None:
        if role not in VALID_ROLES:
            raise ValueError(f"invalid role: {role}")
        if not isinstance(content, str):
            raise TypeError("content must be str")
        message = {"role": role, "content": content}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        self._messages.append(message)

    def append_pair(self, user_content: str, assistant_content: str) -> None:
        """整回合作为一条 JSONL 事务落盘，断电不会留下悬空 user。"""
        self.append_batch([
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ])

    def append_batch(self, messages: list[dict[str, str]],
                     transaction_id: str | None = None) -> None:
        """一次刷盘提交一个完整行动回合，避免只持久化其中一部分。"""
        if transaction_id is not None and transaction_id in self._transactions:
            return
        normalized: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in VALID_ROLES:
                raise ValueError(f"invalid role: {role}")
            if not isinstance(content, str):
                raise TypeError("content must be str")
            normalized.append({"role": role, "content": content})
        if not normalized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {"messages": normalized}
        if transaction_id is not None:
            record["transaction_id"] = transaction_id
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        self._messages.extend(normalized)
        if transaction_id is not None:
            self._transactions.add(transaction_id)

    def has_transaction(self, transaction_id: str) -> bool:
        return transaction_id in self._transactions

    def messages(self) -> list[dict[str, str]]:
        return [dict(message) for message in self._messages]


def run_persistent_turn(store: BrainSessionStore, user_content: str, model: Any) -> str:
    """先获得完整模型回答，再将一对消息落盘；传输失败时会话保持原样。"""
    messages = [*store.messages(), {"role": "user", "content": user_content}]
    assistant_content = model(messages)
    if not isinstance(assistant_content, str) or not assistant_content:
        raise ValueError("model returned empty assistant content")
    store.append_pair(user_content, assistant_content)
    return assistant_content
