# Numen 主动行为循环：部署与使用

守护进程自动处理：玩家提及名字 → 回话；自己死亡 → 说明；生命/饥饿偏低 → 生存处理。

## 快速开始（测试）

```bash
cd ~/.hermes/workspace/numen-ab-lab
# 先填环境变量（MCP URL/token、模型端点二选一）
export NUMEN_MCP_URL=http://127.0.0.1:25585/mcp        # 隔离服
export NUMEN_MCP_TOKEN=<token>

# 模式 A：DeepSeek 官方（推荐用于云端推理）
export NUMEN_DEEPSEEK_KEY=<key>
# 模式 B：LM Studio 本地模型（OpenAI 兼容端点；无需 key）
# export NUMEN_LLM_URL=http://127.0.0.1:1234/v1/chat/completions
# export NUMEN_LLM_MODEL=qwen2.5-9b-instruct

# 跑一轮（不常驻）
python3 active_agent_cli.py --once --companions ABBrain --mention-words ABBrain,八千代
```

## 正式服部署（systemd 用户服务）

```bash
cd ~/.hermes/workspace/numen-ab-lab/deploy
cp .env.active-agent.example ../.env.active-agent
# 编辑 ../.env.active-agent：填正式服 MCP token（25567）
# 并选择模型端点：DeepSeek key（A）或本地 LM Studio URL+模型名（B）

# 安装服务
mkdir -p ~/.config/systemd/user
cp numen-active-agent.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now numen-active-agent.service
systemctl --user status numen-active-agent.service
```

## LM Studio 本地模型注意事项

- agent 大脑默认使用 OpenAI 兼容的 `/v1/chat/completions`；LM Studio 的
  Developer 面板开启本地服务器后即可用 `http://127.0.0.1:1234/v1`。
- 本地小模型（如 7B/9B）**能力上限决定任务复杂度**：简单移动、采木、
  拾取、按坐标导航可胜任；长链任务（挖矿→烧炼→合成→入箱）需要更强
  模型或分步下达。
- 选模型时优先支持严格 JSON / tool-calling 的指令模型；`ensure_llm_ready`
  只对 DeepSeek 官方端点强制要 key，本地端点无需 key。

日志：

```bash
journalctl --user -u numen-active-agent.service -f
```

## 行为与参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--poll` | 15s | 事件轮询间隔 |
| `--cooldown` | 120s | 同一同伴自动回合最小间隔（防刷屏/控费） |
| `--survival-interval` | 300s | 生命/饥饿检查间隔 |
| `--mention-words` | 同伴名 | 聊天触发词（含任一即回话） |
| `--companions` | 所有 live | 指定驱动哪些同伴 |

- 事件游标存 `cursors/<server>/<uuid>.cursor.json`，重启不重复回应。
- 主动回合与 `brain_server` / CLI 手动回合通过文件锁互斥（同一同伴同一时刻只有一个大脑在写）。
- 只有"提及"或"自己死亡"才触发回话；无关聊天不烧模型费用。
- 首次启动只建立游标，不会拿历史事件刷屏回话。

## 注意

- 需要目标服务器 jar 含 `get_recent_events` 工具（v0.0.10 已含）。
- 正式服需先重启加载新 jar（见仓库 README 部署说明）。
- `.env.active-agent` 含密钥，勿提交 GitHub（已在 tools/numen-ab-lab/.gitignore 覆盖 `.env*`？如未覆盖请补）。
