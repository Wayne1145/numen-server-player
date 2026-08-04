# 阶段 S4 报告 — MCP

日期：2026-07-26 ｜ 工作区：`D:\mcmod\minecraft-numen-server-lab`

## 完成内容

- **服务端 MCP Server**（`server/mcp/ServerMcpServer`，JSON-RPC 2.0 over HTTP，JDK HttpServer，
  无第三方 SDK）：`initialize`、`notifications/initialized`、`ping`、`tools/list`、`tools/call`。
  **全部路由到 S3 的 `ServerNumenActuator`**——不经客户端 `NumenActuator`、不经 C→S payload、
  不绕过 S3 安全收口（policy/lease/限流/审计原样生效）。
- **协议严格校验**：`jsonrpc="2.0"`、request id、params 类型、batch 元素、标准错误码
  （-32700 解析 / -32600 无效请求 / -32601 方法不存在 / -32602 参数 / -32603 内部）；
  notification（无 id）不回包（202），带 id 的畸形请求必回错误——不静默吞。
- **认证与网络安全**（照 §5.5）：仅 `Authorization: Bearer <token>`（经 S3 `AuthTokens`,常量时间）；
  **query-string token 一律拒绝**；默认 `enabled=false`、绑定 `127.0.0.1`（支持 `::1`）；
  **拒绝 0.0.0.0/:: 通配绑定**；非 loopback 且无 token → 拒绝启动;首次启用无 token → 生成
  **256-bit** 高熵 token（记录日志,会话内生效）。
- **资源限制**：body ≤64KiB(413)、batch ≤16、嵌套深度上限、有界工作线程池+队列、
  每 principal 请求限流(429)；**请求超时与任务超时分离**——动作工具立刻返回 `task_id`,
  驱动方轮询 `task_status`,HTTP 不为长任务同步阻塞。
- **工具面**：管理工具（`list/create/delete_companion`、`acquire_control`/`release_control`、
  `task_status`/`task_stop`）+ 引擎工具**按能力白名单过滤**（`tools/list` 只见 policy 启用的能力;
  destructive 关闭时 `mine` 等不出现）。
- **stdio adapter**（`StdioMcpAdapter`）：纯 JDK 的 stdio↔HTTP 桥,供只讲 stdio 的 MCP 宿主
  （Claude Desktop/Hermes）使用：`java -cp <jar> …StdioMcpAdapter <url> <token>`。
- **封闭式（hermetic）协议测试**（`ServerMcpServerTest`）：mock actuator + 真实 loopback HTTP,
  覆盖验收全流程、认证、错误码、限额、绑定拒绝——无 Minecraft、无外网、无模型 API Key。

## 修改文件

- numen-api（`server-fork` @ `d1635289`,tag `s4-mcp`）：
  - 新增 `server/mcp/`：`ServerMcpServer`、`McpNetConfig`、`StdioMcpAdapter`；测试 `ServerMcpServerTest`
  - 改：`server/ServerControlConfig`（`mcp` 配置段 + `generateToken()` + 首启自动铸 token）、
    `forge/NumenMod`（ServerStarted 启 MCP/ServerStopping 停）
- numen-core：**本阶段无改动**（@ `eeb9b710`）
- lab：`scripts/s4-mcp-client.sh`（独立 MCP 客户端）、`docs/reports/s4/`、`artifacts/s4/`

## 构建与测试

```text
numen-api :common:test --tests 'com.dwinovo.numen.server.*'   → BUILD SUCCESSFUL
  （ServerMcpServerTest 7 用例全绿：验收全流程 / 401(无token+错token) / query-token 拒绝 /
    -32700/-32600/-32601 / 64KiB+batch16 限额 / 通配绑定拒绝 / 非loopback无token拒绝;
    加上 S2/S3 的 server.* 全部用例）
numen-api :common:publish :forge:build :forge:publish -x test → BUILD SUCCESSFUL
numen-core :forge:build                                       → BUILD SUCCESSFUL
可部署产物：artifacts/s4/numen-forge-1.20.1-0.0.10-all.jar
  SHA-256 = 677bea0365f67d1ec183e64c266e16120dac5afe8543e3a929a630b0a28367fa
```

## 独立测试服验证（Forge 47.4.0 / Java 21，MCP enabled @127.0.0.1:25567）

服务器日志：`[numen-mcp] server up on http://127.0.0.1:25567/mcp (loopback=true, tokens=set)`。
**独立 MCP 客户端**（`scripts/s4-mcp-client.sh`,仅 bash+curl,与 Minecraft/模型零耦合）全部通过
（`docs/reports/s4/logs/06-s4-mcp-client.log`）：

- 认证：无 Bearer → **401** ✅；query-string token → **401** ✅。
- `initialize` → `numen-server-mcp` ✅；`notifications/initialized` → 202 ✅。
- `tools/list`：含 `acquire_control` ✅、含引擎工具 `goto`（能力过滤后暴露）✅、
  **不含 `mine`**（destructive 默认关）✅。
- `create_companion McpBot` → 入册并 `McpBot joined the game` ✅；`list_companions` 见之 ✅。
- `acquire_control` → lease `296e69b0…` ✅（审计 `op=acquire_control ok=true`）。
- **goto**：起点 (-318,176) → 派发 `task_id=t1` ✅ → `task_status` 轮询 `running→idle` ✅ →
  再感知 `get_self_status`：**终点 (-311,180),身体确实移动** ✅ → `release_control` ✅。
- **审计链完整**：create/list/acquire/get_self_status/goto(t1)/task_status/release 每步都有
  `principal=mcp-client` 的 `numen-audit` 结构化记录。
- **全程无模型 API Key**——MCP → actuator → `onServerCall`,零 LLM 参与 ✅（验收核心）。
- 日志噪声说明：会话尾部 `Bob was shot by Skeleton`、`McpBot was shot by Skeleton` 为夜间骷髅所致的
  **正常生存死亡**,30s 后二者按 S1 无主复活逻辑自动重生（`joined the game` 第二次出现即复活,
  非重复实体;`list` 无重复）。属预期生存行为,非缺陷。

## 发现的问题

- 严重：无。中等：无。
- 低：
  1. **构建网络抖动**：`--refresh-dependencies` 触发 loom 重下 Mojang piston-data 的 client/server
     jar,代理下大文件多次中断（`fixed content-length … bytes received …`）。处置：手动经代理断点续传
     并 **sha1 校验**后放入 `~/.gradle/caches/fabric-loom/1.20.1/`,重跑构建即过。已记录:仅在
     numen-api 升级后才需要 `--refresh-dependencies`,平时不要带。
  2. 测试服在夜间会有怪物攻击 AI 玩家（见上）——对 S5/S6 的长任务验证,建议临时
     `gamerule doMobSpawning false` 或白天化,避免流程被打断（不改变默认策略）。

## 正式服影响

- **未修改生产服务器**：测试仅在隔离的新世界和配置副本中进行。
  正式世界、未改正式 config。MCP 端点仅绑定测试机 loopback。

## Git

- `source/numen-api`：`server-fork` @ `d1635289`（tag `s4-mcp`），clean。
- `source/minecraft-numen`：`server-fork` @ `eeb9b710`（S4 未改），clean。
- lab `master`：本次提交 S4。哈希见提交输出。

## 下一阶段（S5 — API Provider 双入口，待你确认后再开始）

- 服务端可选 API agent（复用上游 `HttpLlmTransport`/`OpenAIProvider`——已确认纯 JDK HTTP、
  无客户端依赖,但需从 `client/` 树提升）;经**同一个** `ServerNumenActuator` + **同一把 lease**
  （API agent 与 MCP 不可同时驱动同一 companion——S3 lease 天然保证）;API Key 仅服务端
  `local.properties`/环境变量,永不入库、不进日志、不回客户端。
- 验收：无 Key → API agent 优雅禁用而 MCP 正常;有 Key → API agent 可驱动;两入口共享 lease 语义。

> 未经你的明确「继续 S5」指令,不跨阶段。
