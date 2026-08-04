# 阶段 S5 报告 — API Provider 双入口

日期：2026-07-27 ｜ 工作区：`D:\mcmod\minecraft-numen-server-lab`

## 完成内容

- **可选的服务端 API agent**（默认关闭）：`server/agent/` 三件套
  - `ApiAgentConfig` —— **API Key 只从服务端解析**：环境变量（默认 `NUMEN_API_KEY`）或服务端本地
    文件（`api_key_file`,gitignored）。**永不**入主配置、不进 git、不进日志、不发往客户端。
    无 Key → `isUsable()=false`,agent 优雅禁用而 MCP 完全不受影响（验收的核心分叉）。
  - `ServerApiAgent` —— 每 companion 一个 LLM 会话循环（worker 线程,绝不占用服务器主线程）：
    **先取与 MCP 同一把 `LeaseRegistry` 写租约**（单写者:双驱动天然不可能）,每个 tool call 都走
    `ServerNumenActuator`（S3 的 policy/限流/审计原样生效）,异步动作轮询 `task_status` 到 idle 再把
    结果喂回模型,turn 预算封顶,**所有退出路径都释放租约**。LLM 传输以 `LlmFn` 注入 → 循环核心可
    在无网络/无 Minecraft/无 Key 下单测。
  - `ServerApiAgentManager` —— 配置闸门 + 真实 `NumenLlmClient` 接线（复用上游纯 JDK 的
    `HttpLlmTransport`/`OpenAIProvider`,Key 在调用时解析、只交给传输层）,工具表按能力策略过滤,
    worker 池,shutdown 清理。
- 配置：`server_control.json` 新增 `api_agent` 段（enabled/provider/model/base_url/api_key_env/
  api_key_file/max_turns/request_timeout_seconds）。
- 测试口：`/numenctl agent status|say <companion> <text>|stop`。
- Forge 接线：ServerStarted 配置 manager;ServerStopping shutdown。

## 修改文件

- numen-api（`server-fork` @ `4a864fa8`,tag `s5-api-agent`）：
  - 新增 `server/agent/`：`ApiAgentConfig`、`ServerApiAgent`、`ServerApiAgentManager`;
    测试 `ServerApiAgentTest`
  - 改：`server/ServerControlConfig`（`api_agent` 段）、`server/ServerActuatorCommands`
    （`agent` 子命令）、`forge/NumenMod`（configure/shutdown）
- numen-core：**本阶段无改动**（@ `eeb9b710`）
- lab：`scripts/s5-mock-llm.py`（本地 OpenAI 兼容 SSE 假端点,仅测试用）、`docs/reports/s5/`、`artifacts/s5/`

## 构建与测试

```text
numen-api :common:test --tests 'com.dwinovo.numen.server.*'   → BUILD SUCCESSFUL
  ServerApiAgentTest 3 用例全绿：
   - loopRunsToolsThenFinishes:先 acquire,工具经 actuator（goto 轮询到 idle）,最后 release
   - turnBudgetBoundsTheLoop:预算封顶,预算耗尽也 release
   - leaseContentionFailsFastAndClean:租约被占 → 快速失败,一个工具都不会执行
  （加上 S2–S4 的全部 server.* 用例）
numen-api :common:publish :forge:build :forge:publish -x test → BUILD SUCCESSFUL
numen-core :forge:build --refresh-dependencies                → BUILD SUCCESSFUL(loom 缓存已预置)
可部署产物：artifacts/s5/numen-forge-1.20.1-0.0.10-all.jar
  SHA-256 = 27179d0df10f42d464e596b2db9e4b648bf99ca6572f83fea43c3e2ea952c986
```

## 独立测试服验证（Forge 47.4.0 / Java 21）

**Phase A — 无 Key**（`logs/03-s5-nokey.log`,`api_agent.enabled=true` 但环境无 `NUMEN_API_KEY`）：
- 启动日志：`[numen-agent] api_agent enabled but NO key resolvable (env NUMEN_API_KEY / file '') —
  agent stays disabled; MCP is unaffected` ✅
- `numenctl agent status` → `UNAVAILABLE`;`agent say` → 明确拒绝 ✅
- **MCP 服务器照常上线**（`server up on http://127.0.0.1:25567/mcp`）✅ —— 无 Key 时 MCP 不受影响。

**Phase B — 有 Key**（`logs/05-s5-withkey.log`,`NUMEN_API_KEY=test-key-123` 环境注入,指向本地
OpenAI 兼容 SSE 假端点 `scripts/s5-mock-llm.py`,Bearer 必检）：
- `api_agent ready (provider=openai, model=mock-model) — key sourced server-side only` ✅
- `numenctl agent say McpBot walk east a bit` →
  审计 `principal=api-agent op=acquire_control ok=true` →
  `[numen-llm] client initialised … url=http://127.0.0.1:25580/v1/chat/completions, streaming=true` →
  模型回 `tool_calls=[goto]` → 审计 `op=goto task=t1` → `task_status` 轮询 6 次至 idle →
  模型收尾 `content="arrived at the target"` → `op=release_control ok=true` →
  控制台输出 `[api-agent] arrived at the target` ✅
- **身体确实移动**：`data get entity McpBot Pos` 前后 (-320.4, 93, 172.9) → **(-305.7, 88, 183.3)**
  （目标 -306,183,寻路到位）✅ —— **真实 LLM 协议栈**（上游 SSE 传输+OpenAI provider）驱动成功。
- **Key 卫生**：`test-key-123` 在服务器日志出现 **0 次** ✅。

**Phase C — 租约互斥（API agent 与 MCP 不可同驾一体）**：
- agent 持锁期间,`numenctl acquire McpBot`（模拟 MCP 侧 acquire）→
  `acquire failed: companion is already controlled by another principal` ✅
- agent release 后再 acquire → 成功（fence 2,单调）✅ —— 同一把租约、单写者语义贯通两个入口。

## 发现的问题

- 严重：无。中等：无。
- 低：验证用 mock LLM 端点只覆盖 OpenAI 兼容面（上游 provider 栈本就以 OpenAIProvider 为参考实现);
  接真实厂商仅是换 `base_url`/model/Key,无代码路径差异。真实厂商联调留给上线前人工验收。

## 正式服影响

- **未修改正式服**。API agent 只连测试机 loopback 假端点;无任何真实 Key 使用或外发。

## Git

- `source/numen-api`：`server-fork` @ `4a864fa8`（tag `s5-api-agent`），clean。
- `source/minecraft-numen`：@ `eeb9b710`（S5 未改），clean。
- lab `master`：本次提交 S5。哈希见提交输出。

## 下一阶段（S6 — Mohist 兼容 + 生产模拟，待你确认后再开始）

顺序：**空白 Forge**（已有）→ **空白 Mohist**（test-env/mohist-light,Mohist 1.20.1 + Java 21）→
**正式服模组集副本**（test-env/modpack-copy,复制正式服 mods/config 到隔离目录,绝不动原目录）。
每级验证：启动无错、召唤/MCP 控制/死亡复活/重启存续、与 ~266 模组共存无冲突。产出 S6 报告 +
最终交付清单。**仍然绝不接触正式服本体。**

> 未经你的明确「继续 S6」指令,不跨阶段。
