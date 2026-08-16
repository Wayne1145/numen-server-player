# numen-ab-lab

Numen 外部大脑的隔离 A/B 实验区：真实 Mohist/Forge 1.20.1 服务器 + MCP 身体 +
OpenAI-compatible 模型路由。仓库内保留的是**干净公开版**：凭据全部走环境变量，
运行数据（sessions / results / checkpoints / locks）不提交。

## 目录内容

- `brain_runner.py` — A/B 运行器：固定任务、短租约、模型 JSON 决策、评分。
- `action_brain.py` — 持久行动大脑运行时：整回合单事务提交、checkpoint 恢复、
  精确 `task_id` 终态、每 UUID 进程锁。
- `persistent_brain.py` — JSONL 会话存储：按 `server_id + companion UUID` 分区，
  单记录事务 + `turn_id` 幂等。
- `persistent_action_cli.py` — 命令行入口：`--message` 提交请求，`--recover` 恢复未完成回合。
- `persistent_chat.py` — 纯记忆对话的最小入口（无工具）。
- `world_memory.py` — 世界记忆：`remember_block`/`recall_blocks` 按同伴隔离。
- `event_inbox.py` — 服务器事件收件箱（回合开始附加，成功提交后消费）。
- `brain_server.py` — 常驻 HTTP API + Web UI（多服路径、Bearer 认证）。
- `active_agent.py` — 主动行为循环：事件游标、提及/死亡触发、生存检查、冷却。
- `active_agent_cli.py` — 主动守护进程入口（`--once` 单轮调试 / `run_forever` 常驻）。
- `deploy/` — systemd 服务文件、env 模板、部署文档。
- `webui/` — 同伴控制台前端（对话/历史/记忆/事件）。
- `personas/` `skills/` — 角色卡与技能库（`--persona`、`load_skill`）。
- `terminal_result_acceptance.py` — MCP 任务终态（done/failed/stopped）隔离验收脚本。
- `test_*.py` — 全部单元测试（当前 72/72 通过）。
- `cases.json` — A/B 任务定义。
- `server_control.example.json` — 服务端 `config/numen/server_control.json` 模板，
  **token 必须替换为随机长字符串**。
- `*ACCEPTANCE.md` / `AUDIT-*.md` / `AB-MOVE-REPORT.md` — 各阶段验收与审计报告。

## 环境变量（全部必填，无默认凭据）

| 变量 | 用途 |
|---|---|
| `NUMEN_MCP_URL` | 例如 `http://127.0.0.1:25585/mcp` |
| `NUMEN_MCP_TOKEN` | 与 `server_control.json` principals.token 一致 |
| `NUMEN_RCON_PORT` | 例如 `25586` |
| `NUMEN_RCON_PASSWORD` | 与 `rcon.password` 一致 |
| `NUMEN_LLM_URL` | OpenAI-compatible 端点 |

## 运行

```bash
python3 -m unittest -q test_action_brain.py test_brain_runner.py \
  test_persistent_brain.py test_persistent_action_cli.py

python3 persistent_action_cli.py --companion ABBrain \
  --message '记住口令，然后走到 x=10.5 z=0.5 并验证'
python3 persistent_action_cli.py --companion ABBrain --recover
```

## 关键契约

- 身份 = `server_id + companion UUID`（显示名只用于发现，不参与记忆键）。
- 完整行动回合作为**一条 JSONL 事务**提交，带 `turn_id`；重复提交幂等。
- 异步动作按原 `task_id` 等到 `done/failed/timeout/stopped`；`idle` 不算成功。
- 模型思考不持身体 Lease；写动作只在使用短租约期间派发。
- 已取得 `task_id` 的崩溃恢复只轮询该任务，**不重放动作**；
  无派发回执的模糊状态拒绝自动恢复。
- 每 UUID 一个非阻塞文件锁，阻止两个大脑并发写同一会话。
- 工具轮询中间样本留在运行结果（审计用），不写入长期历史。
