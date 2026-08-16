# Numen 正式服测试验收路线

目标：验证 v0.0.10（含主动行为循环）在正式服上端到端可用。由 Wayne 执行测试，
发现 bug 时记录"步骤号 + 实际输出 + 期望"反馈。

## 前置（第 0 步，需要你操作）

1. 重启正式服，加载新 jar（`mods/numen-forge-1.20.1-0.0.10-all.jar`，SHA-256
   `78528a55647b7a467552246c3aaf33f60687b9378e7c872ae4f35106e8cd037f`，旧包已备份）。
2. 确认 MCP 端口监听：`netstat -ano | findstr 25567`。
3. 准备正式服 MCP token（`config/numen/server_control.json` 中 principals[0].token）
   与 DeepSeek key。

## 第一层：基础连接（不涉及 AI 智能）

| # | 操作 | 预期 |
|---|---|---|
| 1 | `export NUMEN_MCP_URL=http://127.0.0.1:25567/mcp NUMEN_MCP_TOKEN=<正式服token>`；`python3 numen_mcp_client.py list` | 列出同伴，无 401 |
| 2 | `numen_mcp_client.py status <同伴>` | 返回位置/血量/维度 |
| 3 | 派发 `goto`，按返回 task_id 调 `task_status` | 先 running/queued，后 `done` + 结构化 result |
| 4 | 游戏里**真实玩家**发一条聊天 → `get_recent_events` | 事件列表含该聊天（kind=chat, source=玩家名） |
| 5 | 同一 `client_action_id` 连续两次 `goto` | 第二次 `idempotent:true`、同一 task_id、身体只走一次 |

## 第二层：大脑 + 手动回合

| # | 操作 | 预期 |
|---|---|---|
| 6 | `python3 brain_server.py --port 18777 --token <服务token>`；浏览器打开 `http://127.0.0.1:18777`（填 token） | 页面加载，同伴列表可见 |
| 7 | Web UI 发指令："记住口令 X，然后走到 x=5 z=0" | 模型回合返回，历史记录可见 |
| 8 | 再问："口令是什么？"（同同伴） | 跨回合记忆正确 |

## 第三层：主动行为循环（本次新增）

安装：`cp .env.active-agent.example .env.active-agent`，填正式服 token + DeepSeek key，
然后 `systemctl --user start numen-active-agent.service`（服务已 enable）。

| # | 操作 | 预期 |
|---|---|---|
| 9 | 游戏里喊同伴名（如"八千代"或同伴名） | ≤15s 后同伴自动回话（`journalctl --user -u numen-active-agent -f` 可见） |
| 10 | `kill 同伴名`（游戏内命令） | 复活后自动回话说明死亡 |
| 11 | 连续两次喊名字 | 第一次回话，120s 冷却内第二次不重复回 |
| 12 | 发一条无关聊天（不含名字） | 不回话（事件游标推进，不烧模型费用） |
| 13 | 让同伴饥饿/低血（如长时间不吃） | 生存检查触发自动处理回合 |

## 第四层：稳定性

| # | 操作 | 预期 |
|---|---|---|
| 14 | 常驻运行 ≥24h | 无崩溃；日志无异常堆栈；内存平稳 |
| 15 | 重启正式服 | 服务自动恢复（Restart=on-failure），游标不丢（不重复回话） |

## 已知边界（出现即正常，不算 bug）

- 死亡后 dormant 期间约 30–60s 无法构建上下文（等待复活，下一轮轮询自动恢复）。
- 主动回合与 Web UI/CLI 手动回合互斥（文件锁）：手动操作时自动回话可能等待或跳过。
- 聊天事件依赖真实玩家发送（ServerChatEvent）；`send_chat` 广播不产生事件。
- 正式服 MCP token / DeepSeek key 只存在于本机 `.env.active-agent`（已 gitignore）。
