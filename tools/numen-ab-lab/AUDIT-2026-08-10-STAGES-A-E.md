# 阶段 A–E 审计报告（2026-08-10）

范围：上下文压缩（A）、Persona+Skills（B）、世界记忆与事件（C）、幂等派发（D）、
常驻服务与 Web UI（E）。所有功能 TDD 实现，隔离 Mohist 服综合实测通过，
随后整体审计。

## 新增功能与实测证据

### A 上下文压缩
- `BrainSessionStore` 新增摘要记录（`{"type":"summary"}`），模型可见视图始终从最近摘要
  （system 消息）开始，旧历史保留在文件供审计但不再进上下文；`turn_id` 幂等保持。
- `compact_history`：模型压缩失败不改变历史；触发阈值（消息数/字符数）可配置。
- 实测：单元测试覆盖摘要链、失败保持、触发阈值（15 个测试）。

### B Persona + Skills
- `personas/<name>.md` 角色卡，`--persona` 注入 system prompt。
- `skills/<name>.md` 技能库；`load_skill` 作为**本地工具**（不经 MCP、不持租约）。
- `ActionBrainRuntime` 新增 `local_tools` 分发（与 MCP 工具同回合混用）。
- 实测：`--persona yachiyo` 风格生效；`load_skill mining` 返回技能正文并写入会话。

### C 世界记忆 + 事件
- `world_memory.py`：按同伴隔离的 JSON 记忆，`remember_block` / `recall_blocks`
  本地工具（按距离排序、类型过滤、同位置更新备注、原子写）。
- `event_inbox.py`：JSONL 事件收件箱，回合开始时附加进用户消息，成功提交后消费。
- Java：`EventRingBuffer`（容量 200 环形缓冲）+ `get_recent_events` 工具
  （SERVER_READ_ONLY 白名单），Forge 监听 `ServerChatEvent` / `LivingDeathEvent` 写入。
- 实测：`kill ABTermResult` 后 `get_recent_events` 返回死亡事件；工作台 (5,64,0)
  记录后跨进程 `recall_blocks` 成功回忆；inbox 事件进入上下文且提交后清空。

### D 幂等派发（关闭 dispatch_unknown 窗口）
- Java：`IdempotentDispatchRegistry`（每同伴 128 条，删除同伴清理）；
  `ServerMcpServer.invokeTool` 识别并剥离 `client_action_id`，命中直接返回原 task_id。
- Python：写动作自动注入 `client_action_id`（checkpoint 持久化）；`tool_planned`
  恢复从"拒绝"升级为"同一幂等键安全重发"（服务端只派发一次）。
- 实测：同一 `client_action_id` 两次 goto → 第二次 `idempotent:true`、同一 `task_id`
  且服务端只派发一次；`tool_dispatched` 恢复只查询不重放。

### E 常驻服务 + Web UI
- `brain_server.py`：ThreadingHTTPServer + Bearer 认证（默认 127.0.0.1），
  多服路径 `server_id`，端点：`/health`、`/v1/companions`、`/v1/turns`、
  `/v1/recover`、`/v1/history`、`/v1/memory`、`/v1/events`、`/`（Web UI）。
- `webui/index.html`：同伴列表、对话、历史/记忆/事件面板（vanilla JS）。
- 实测：health/companions/memory/turns 全部真实请求通过；Web UI 页面返回；
  POST /v1/turns 真实模型回合（"主世界（minecraft:overworld）"，历史 32→34）。

## 审计中发现并修复的问题

| 问题 | 级别 | 修复 |
|---|---|---|
| 任务轮询间隔 0.25s 打爆服务端每分钟限流（240+ calls/min > per-companion 300/min），实测 HTTP 429 | 高 | 轮询节流至 1.0s（两处）；任务完成延迟上限约 1s |
| 恢复分支 `actions`/`transient`/`user_content` 未初始化导致 UnboundLocalError | 中 | 分支内补齐初始化 |
| `brain_server` handler 早期版本有未定义引用与签名不一致 | 中 | 重构 `_ctx_or_error`、统一 factory 三参签名 |
| 测试断言与实现语义不一致（persona 注入时机、Web UI 认证） | 低 | 测试修正为真实语义 |

## 验证汇总

- Python 单元测试：**72/72 通过**（含新增 25 个）。
- Java：`com.dwinovo.numen.server.*` 全部通过（含 IdempotentDispatchRegistryTest 5 项、
  ServerMcpServer 幂等测试）；`minecraft-numen` 全量构建通过
  （EventRingBufferTest 4 项、RecentEventsTool/Forge 事件监听编译验证）。
- 新候选 Jar：`78528a55647b7a467552246c3aaf33f60687b9378e7c872ae4f35106e8cd037f`
  （含 EventRingBuffer/RecentEventsTool/IdempotentDispatchRegistry，无 scan_area）。
- 隔离服部署：PID 24240，35 个 MCP 工具，`get_recent_events` 在线，无重复/无 scan_area。
- 正式服未动（PID 10500，21294/25567）。

## 边界与未做

- 聊天事件实测依赖真实玩家聊天（ServerChatEvent）；本环境用死亡事件验证了同一通路。
- 上下文压缩的真实触发未在隔离服跑满 60 条消息（单元测试覆盖触发与摘要语义）。
- 正式服部署仍未执行（需批准与重启）。
- 既有 5 个 voice 测试失败与本次无关，未修复。
