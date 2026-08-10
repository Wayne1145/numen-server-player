#!/usr/bin/env python3
"""真实模型的单次持久会话入口；每次启动都是新进程。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from brain_runner import LLM_URL, LLM_MODEL, LLM_HEADERS, ensure_llm_ready, post_json, retry_transient
from persistent_brain import BrainSessionStore, run_persistent_turn

SYSTEM_PROMPT = """你是与一个 Minecraft Numen 身体绑定的长期同伴大脑。
这次只进行记忆对话，不调用游戏工具。简体中文简短回答；忠实使用历史，不编造。"""


def model_call(messages: list[dict[str, str]]) -> str:
    ensure_llm_ready()
    def request_once():
        return post_json(LLM_URL, {
            "model": LLM_MODEL,
            "stream": False,
            "temperature": 0,
            "max_tokens": 120,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        }, headers=LLM_HEADERS, timeout=150)

    response = retry_transient(request_once, attempts=2, delay_seconds=3)
    message = response["choices"][0]["message"]
    return message.get("content") or message.get("reasoning_content") or ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-id", default="numen-ab")
    parser.add_argument("--companion-id", required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    store = BrainSessionStore(Path(__file__).resolve().parent / "sessions",
                              args.server_id, args.companion_id)
    before = len(store.messages())
    reply = run_persistent_turn(store, args.message, model_call)
    print(json.dumps({
        "companion_id": args.companion_id,
        "history_before": before,
        "history_after": len(store.messages()),
        "reply": reply,
        "session_path": str(store.path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
