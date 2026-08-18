#!/usr/bin/env python3
"""每个 Minecraft 同伴独立的持久大脑会话。"""
from __future__ import annotations

import json
import os
import re
import uuid
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
    """以 server_id + companion UUID 分区的追加式 JSONL 会话。

    记录类型：消息批次 {"messages":[...], "transaction_id"?}、旧式单消息
    {"role","content"}、摘要 {"type":"summary","content"}。模型可见视图
    始终从最近一条摘要开始（作为 system 消息），摘要之前的旧历史只保留
    在文件里供审计，不再进入上下文。
    """

    def __init__(self, root: Path, server_id: str, companion_id: str) -> None:
        _reject_path_separators(server_id, companion_id)
        self.path = (root / safe_segment(server_id)
                     / f"{safe_segment(companion_id)}.jsonl")
        self._transactions: set[str] = set()
        self._records: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
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
            if not isinstance(record, dict):
                raise ValueError(f"invalid persisted record at line {index + 1}")
            transaction_id = record.get("transaction_id")
            if isinstance(transaction_id, str) and transaction_id:
                self._transactions.add(transaction_id)
            if record.get("type") == "summary":
                if not isinstance(record.get("content"), str):
                    raise ValueError(f"invalid summary record at line {index + 1}")
                self._records.append({"type": "summary", "content": record["content"]})
                continue
            batch = record.get("messages")
            records = batch if isinstance(batch, list) else [record]
            for message in records:
                if (not isinstance(message, dict) or message.get("role") not in VALID_ROLES
                        or not isinstance(message.get("content"), str)):
                    raise ValueError(f"invalid persisted message at line {index + 1}")
                self._records.append(
                    {"role": message["role"], "content": message["content"]})

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
        self._records.append(message)

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
        self._records.extend(normalized)
        if transaction_id is not None:
            self._transactions.add(transaction_id)

    def append_summary(self, content: str, transaction_id: str | None = None) -> None:
        """追加一条上下文摘要；模型可见视图将从这条摘要开始。"""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("summary content must be a non-empty string")
        if transaction_id is not None and transaction_id in self._transactions:
            return
        record: dict[str, Any] = {"type": "summary", "content": content}
        if transaction_id is not None:
            record["transaction_id"] = transaction_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        self._records.append(record)
        if transaction_id is not None:
            self._transactions.add(transaction_id)

    def has_transaction(self, transaction_id: str) -> bool:
        return transaction_id in self._transactions

    def summary(self) -> str | None:
        """最近一条摘要文本；没有摘要时返回 None。"""
        for record in reversed(self._records):
            if record.get("type") == "summary":
                return record["content"]
        return None

    def messages(self) -> list[dict[str, str]]:
        """模型可见视图：最近摘要(system) + 其后全部消息。

        延迟查询工具现已有 retained task_id，历史不再做工具名清洗；旧失败
        结果作为升级前事实保留，避免篡改可审计会话。
        """
        start = 0
        for index, record in enumerate(self._records):
            if record.get("type") == "summary":
                start = index + 1
        visible: list[dict[str, str]] = []
        summary_text = self.summary()
        if summary_text is not None:
            visible.append({"role": "system", "content": summary_text})
        for record in self._records[start:]:
            visible.append({"role": record["role"], "content": record["content"]})
        return visible

    def should_compact(self, max_messages: int = 60, max_chars: int = 18000) -> bool:
        """模型可见视图是否超过消息数或字符预算，需要压缩。"""
        visible = self.messages()
        if len(visible) > max_messages:
            return True
        return sum(len(message["content"]) for message in visible) > max_chars


def compact_history(store: BrainSessionStore, model: Any) -> str | None:
    """用模型把当前可见历史压缩成摘要；失败时保持历史原样并返回 None。"""
    try:
        visible = store.messages()
        summary_text = model(visible)
        if not isinstance(summary_text, str) or not summary_text.strip():
            raise ValueError("model returned empty summary")
    except Exception:
        return None
    store.append_summary(summary_text, transaction_id=str(uuid.uuid4()))
    return summary_text


def run_persistent_turn(store: BrainSessionStore, user_content: str, model: Any) -> str:
    """先获得完整模型回答，再将一对消息落盘；传输失败时会话保持原样。"""
    messages = [*store.messages(), {"role": "user", "content": user_content}]
    assistant_content = model(messages)
    if not isinstance(assistant_content, str) or not assistant_content:
        raise ValueError("model returned empty assistant content")
    store.append_pair(user_content, assistant_content)
    return assistant_content
