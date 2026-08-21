#!/usr/bin/env python3
"""
Numen 假人大脑监控面板 —— 纯只读、无副作用。
读取 Numen 大脑的会话、事件、游标、checkpoint 等数据，输出 JSON API + 单页 HTML。
启动: python3 numen_monitor.py [--port 8788]
"""
import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER_ID = "numen-ab"
COMPANION_UUID = "1676d7f9-19e4-4107-8c75-dc5eee69daa8"
COMPANION = "yachiyo"

SESSIONS = ROOT / "sessions" / SERVER_ID / f"{COMPANION_UUID}.jsonl"
CURSOR = ROOT / "cursors" / SERVER_ID / f"{COMPANION_UUID}.cursor.json"
CHECKPOINT = ROOT / "checkpoints" / SERVER_ID / f"{COMPANION_UUID}.json"
INBOX = ROOT / "inbox" / SERVER_ID / f"{COMPANION_UUID}.jsonl"
MEMORY = ROOT / "memory" / SERVER_ID / f"{COMPANION_UUID}.json"


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def read_jsonl(path: Path):
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return rows


def fmt_ts(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%m-%d %H:%M:%S")
    except Exception:
        return str(ms)


def parse_tool_call(content: str):
    """assistant 消息里可能是工具调用 JSON 或最终文本。"""
    if not content:
        return None, None
    try:
        obj = json.loads(content)
        if isinstance(obj, dict) and obj.get("name") and obj.get("type") == "tool":
            return obj["name"], obj.get("arguments", {})
    except Exception:
        pass
    return None, content


def parse_tool_result(content: str):
    """user 消息里可能是 <tool_result name=...> 或任务指令。"""
    if content and content.startswith("<tool_result"):
        import re
        m = re.match(r'<tool_result name="([^"]+)">', content)
        name = m.group(1) if m else "?"
        body = content[m.end():] if m else content
        return name, body[:300]
    return None, content


def build_status():
    """进程与服务的实时状态。"""
    out = {"service": None, "pid": None, "cpu": None, "rss": None}
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "numen-active-agent.service"],
            capture_output=True, text=True, timeout=5)
        out["service"] = r.stdout.strip()
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["pgrep", "-f", "active_agent_cli"], capture_output=True, text=True, timeout=5)
        pids = [p for p in r.stdout.split() if p.strip()]
        if pids:
            pid = pids[0]
            out["pid"] = pid
            r2 = subprocess.run(["ps", "-o", "pcpu=,rss=", "-p", pid],
                                capture_output=True, text=True, timeout=5)
            parts = r2.stdout.split()
            if len(parts) >= 2:
                out["cpu"] = parts[0]
                out["rss"] = f"{int(parts[1]) // 1024}MB"
    except Exception:
        pass
    return out


def build_events():
    """从会话/事件文件提取最近的 chat / death 事件。"""
    events = []
    # 优先从会话 JSONL 提取用户指令与结果
    rows = read_jsonl(SESSIONS)
    for row in rows[-8:]:
        for m in row.get("messages", []):
            content = str(m.get("content", ""))
            if m.get("role") == "user" and content and not content.startswith("<tool_result"):
                events.append({"time": fmt_ts(time.time() * 1000), "kind": "chat",
                               "detail": content[:100], "source": "session"})
    # 再读事件游标
    cur = read_json(CURSOR)
    if cur:
        events.append({"time": fmt_ts(cur.get("time", 0)), "kind": "cursor",
                       "detail": str(cur.get("detail", ""))[:100], "source": "cursor"})
    return events[-20:]


def build_turns():
    """把会话 JSONL 解析成可读的回合列表（含工具调用链）。"""
    rows = read_jsonl(SESSIONS)
    turns = []
    for row in rows[-10:]:
        msgs = row.get("messages", [])
        if not msgs:
            continue
        steps = []
        for m in msgs:
            content = str(m.get("content", ""))
            role = m.get("role")
            if role == "assistant":
                name, args = parse_tool_call(content)
                if name:
                    steps.append({"type": "tool", "name": name,
                                  "args": json.dumps(args, ensure_ascii=False)[:200]})
                else:
                    steps.append({"type": "text", "name": "回复",
                                  "args": (content or "")[:300]})
            elif role == "user" and content.startswith("<tool_result"):
                name, body = parse_tool_result(content)
                steps.append({"type": "result", "name": name, "args": (body or "")[:200]})
        turns.append({"tid": str(row.get("transaction_id", ""))[:16],
                      "n_steps": len(steps), "steps": steps})
    return turns


def build_llm_config():
    """大脑当前 LLM 配置（脱敏）与最近一次调用结果。"""
    cfg = {}
    try:
        for line in (ROOT / ".env.active-agent").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("NUMEN_LLM_URL"):
                cfg["url"] = line.split("=", 1)[1]
            elif line.startswith("NUMEN_LLM_MODEL"):
                cfg["model"] = line.split("=", 1)[1]
            elif line.startswith("NUMEN_LLM_KEY"):
                v = line.split("=", 1)[1]
                cfg["key"] = v[:6] + "***" if len(v) > 6 else "***"
    except Exception:
        pass
    return cfg


def build_snapshot():
    snap = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "companion": COMPANION,
        "server_id": SERVER_ID,
        "status": build_status(),
        "cursor": read_json(CURSOR),
        "checkpoint": read_json(CHECKPOINT),
        "events": build_events(),
        "turns": build_turns(),
        "llm": build_llm_config(),
        "inbox_lines": len(read_jsonl(INBOX)),
    }
    return snap


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>八千代 · Numen 大脑监控</title>
<style>
:root { --bg:#0f1115; --card:#171b22; --line:#262c36; --text:#e6e9ef; --muted:#8b93a3;
  --green:#3fb96f; --red:#e05252; --amber:#d9a13b; --blue:#5b8ff9; --purple:#a06bd9; }
* { box-sizing:border-box; margin:0; padding:0; }
body { background:var(--bg); color:var(--text); font:14px/1.6 "PingFang SC","Microsoft YaHei",sans-serif; padding:20px; }
h1 { font-size:20px; margin-bottom:4px; }
.sub { color:var(--muted); font-size:12px; margin-bottom:20px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; }
.card h2 { font-size:14px; color:var(--muted); margin-bottom:10px; font-weight:600; }
.badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; margin-left:6px; }
.badge.active { background:rgba(63,185,111,.15); color:var(--green); }
.badge.dead { background:rgba(224,82,82,.15); color:var(--red); }
.badge.warn { background:rgba(217,161,59,.15); color:var(--amber); }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:500; }
.step { display:flex; gap:8px; padding:5px 0; border-bottom:1px dashed var(--line); font-size:12.5px; }
.step .tag { flex-shrink:0; min-width:64px; text-align:center; border-radius:6px; padding:1px 6px; font-size:11px; }
.tag.tool { background:rgba(91,143,249,.15); color:var(--blue); }
.tag.result { background:rgba(63,185,111,.15); color:var(--green); }
.tag.text { background:rgba(160,107,217,.15); color:var(--purple); }
.step .name { flex-shrink:0; color:var(--blue); font-family:monospace; }
.step .args { color:var(--muted); word-break:break-all; }
.evt { display:flex; gap:8px; padding:4px 0; font-size:12.5px; border-bottom:1px dashed var(--line); }
.evt .t { color:var(--muted); flex-shrink:0; font-family:monospace; }
.evt .k { flex-shrink:0; min-width:52px; text-align:center; border-radius:6px; font-size:11px; padding:1px 4px; }
.k.chat { background:rgba(63,185,111,.15); color:var(--green); }
.k.death { background:rgba(224,82,82,.15); color:var(--red); }
.k.cursor { background:rgba(217,161,59,.15); color:var(--amber); }
.evt .d { color:var(--text); word-break:break-all; }
.kv { display:grid; grid-template-columns:96px 1fr; gap:4px 10px; font-size:13px; }
.kv dt { color:var(--muted); }
.kv dd { font-family:monospace; word-break:break-all; }
.auto { color:var(--muted); font-size:12px; margin-top:16px; }
</style>
</head>
<body>
<h1>八千代 · Numen 大脑监控 <span id="st"></span></h1>
<div class="sub" id="gen"></div>
<div class="grid">
  <div class="card"><h2>进程与服务</h2><dl class="kv" id="kv-status"></dl></div>
  <div class="card"><h2>LLM 配置</h2><dl class="kv" id="kv-llm"></dl></div>
  <div class="card"><h2>游标 / Checkpoint</h2><dl class="kv" id="kv-cur"></dl></div>
  <div class="card"><h2>最近事件</h2><div id="evts"></div></div>
</div>
<div class="card" style="margin-top:14px"><h2>最近回合 · 工具调用链</h2><div id="turns"></div></div>
<div class="auto">每 5 秒自动刷新 · 只读监控 · 不发送任何指令</div>
<script>
async function load(){
  try{
    const r = await fetch('/api/snapshot');
    const d = await r.json();
    document.getElementById('gen').textContent = '生成于 ' + d.generated_at + ' · 同伴 ' + d.companion + ' · server ' + d.server_id;
    // 状态
    const st = d.status;
    const stEl = document.getElementById('st');
    stEl.className = 'badge ' + (st.service==='active'?'active':'dead');
    stEl.textContent = st.service==='active' ? '服务运行中' : '服务未运行';
    document.getElementById('kv-status').innerHTML =
      `<dt>systemd</dt><dd>${st.service||'?'}</dd>` +
      `<dt>PID</dt><dd>${st.pid||'-'}</dd>` +
      `<dt>CPU</dt><dd>${st.cpu||'-'}%</dd>` +
      `<dt>内存</dt><dd>${st.rss||'-'}</dd>` +
      `<dt>inbox 行数</dt><dd>${d.inbox_lines||0}</dd>`;
    // LLM
    const llm = d.llm||{};
    document.getElementById('kv-llm').innerHTML =
      `<dt>URL</dt><dd>${llm.url||'-'}</dd>` +
      `<dt>模型</dt><dd>${llm.model||'-'}</dd>` +
      `<dt>Key</dt><dd>${llm.key||'-'}</dd>`;
    // 游标/checkpoint
    const cur = d.cursor||{}; const ck = d.checkpoint||{};
    document.getElementById('kv-cur').innerHTML =
      `<dt>游标</dt><dd>${(cur.detail||'-').slice(0,60)}</dd>` +
      `<dt>游标时间</dt><dd>${cur.time?new Date(+cur.time).toLocaleString():'-'}</dd>` +
      `<dt>checkpoint</dt><dd>${ck.state||'无'}</dd>` +
      `<dt>turn_id</dt><dd>${(ck.turn_id||'').slice(0,20)||'-'}</dd>`;
    // 事件
    const evts = d.events||[];
    document.getElementById('evts').innerHTML = evts.length ? evts.slice().reverse().map(e=>
      `<div class="evt"><span class="t">${e.time||''}</span><span class="k ${e.kind}">${e.kind}</span><span class="d">${(e.detail||'').slice(0,90)}</span></div>`
    ).join('') : '<div class="evt">暂无事件</div>';
    // 回合
    const turns = d.turns||[];
    document.getElementById('turns').innerHTML = turns.length ? turns.map(t=>
      `<div style="margin-bottom:12px"><div style="color:var(--muted);font-size:12px;margin-bottom:4px">回合 ${t.tid} · ${t.n_steps} 步</div>` +
      t.steps.map(s=>`<div class="step"><span class="tag ${s.type}">${s.type}</span><span class="name">${s.name||''}</span><span class="args">${(s.args||'').slice(0,120)}</span></div>`).join('') +
      `</div>`).join('') : '<div class="evt">暂无回合</div>';
  }catch(e){ document.getElementById('gen').textContent = '加载失败: ' + e; }
}
setInterval(load, 5000); load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/snapshot":
            snap = build_snapshot()
            body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8788)
    args = ap.parse_args()
    print(f"[numen-monitor] http://127.0.0.1:{args.port}/  (Ctrl+C 退出)")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
