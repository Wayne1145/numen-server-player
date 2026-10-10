# Numen：Serena 索引与全项目审计报告

## 结论

> 本文保留 `ce6ea14` 审计起点的发现；后续修复、最终门禁与生产边界见 [0.0.11 修复验收记录](audit-fixes-0.0.11.md)。不要把历史“未修复”条目当成当前代码状态。

本轮完成项目隔离索引、核心链路审计、基线测试和仓库外边界复现。**现有正常对话验收仍有效，但不足以证明取消、并发重试、崩溃恢复和多人/多进程使用可靠。**发现若干此前未覆盖的确认缺陷，优先修取消与幂等，再修持久化和恢复契约。

本轮仅添加索引配置、项目记忆和本报告；未修改业务源码、未替换正式 JAR、未重启/停止 ActiveAgent、未执行正式世界动作。

## 1. 范围与版本证据

- 仓库：`/mnt/d/numen-server-player`，分支 `main`。
- 审计 HEAD：`ce6ea14cf7dd604471ba88a938d40bf6a5b1692e`。
- 开始时工作树无业务修改；结束时新增 `.serena/` 与审计文档。
- 外部大脑运行副本：`/home/wayne/.hermes/workspace/numen-ab-lab`，所有仓库 Python 文件逐文件比较一致。
- 正式服当前目录：`C:/落幕曲1.6.4/fuwuduan`（不是 D 盘历史备份）。
- 正式磁盘 JAR SHA-256：`e607107b32bd0bdc77918130dfbcb1a8dd295c4a7805c7afb9e110496eac6091`。
- ActiveAgent 实查为 `active`。此次不借磁盘 SHA 声称重新证明 JVM 内存版本，也不产生生产副作用。

## 2. Serena 的实际接入方式

原有 `serena-hud` 固定工程为 studio-usage-hud，本轮没有激活/切换它的项目，也没有改 Hermes MCP 配置。

给原仓库创建 `.serena/project.yml`：Java + Python，排除 build、Gradle 缓存、JAR、Maven 产物、环境密钥、会话/checkpoint/inbox/cursor。

默认 JDTLS 会发现 Windows Forge/Fabric Gradle 工程并执行依赖导入；本机 WSL 网络/构建环境使这一过程超时且语言服务器退出。为避免把审计变为整套开发环境迁移，建立 **源码镜像** `/home/wayne/.hermes/workspace/numen-serena-source`，仅复制跟踪的源码及必要文本契约，并保存 `source-manifest.json`。544 个 Java/Python 文件最终对原仓库与镜像都进行了 SHA-256 比较，差异为 0。

索引输出明确为：

```text
Indexed files per language: java=521, python=23
544/544
```

实际使用并验证：`get_symbols_overview`、`find_symbol(include_body=true)`、`find_referencing_symbols`、`search_for_pattern`。例：从 `ActionBrainRuntime/recover_turn` 查到 `CompanionContext/recover_turn` 对 `result['final']` 的错误假设；从 `EventInbox/clear` 查到提交与清理路径。

**限制**：Java 镜像没有完整 Minecraft classpath，符号轮廓/源码定位可用，但 Java 跨模块引用可能返回空。空结果不是“无人调用”的证据，已补精确文本契约检查。Serena 是导航工具，不是自动给出无 bug 认证的扫描器。

## 3. 架构地图

```text
玩家 /numen say
  → DebugCommands.sayToAI
  → RecentEventsTool.BUFFER / EventRingBuffer
  → ActiveAgent.run_forever / run_once
  → EventCursor + EventInbox
  → CompanionContext（UUID、进程锁、会话、世界记忆、工具列表）
  → ActionBrainRuntime（模型决策、checkpoint、派发、等待、提交）
  → McpClient / JSON-RPC tools/call
  → ServerMcpServer（Bearer、资源限制、同伴解析、幂等）
  → ServerNumenActuatorImpl（主线程、策略、租约、配额、审计）
  → ToolRegistry / TaskDispatch / CompanionTickDispatcher
  → 原生 ServerPlayer 身体、寻路/采矿/容器/存档
  → retained task_status → 模型 → final → send_chat
```

仓库还保留客户端 UI/网络/声音、Fabric 适配、服务端内置 API Agent、旧可写 BrainServer WebUI；这些不等于正式外部大脑主链。第三方 Command Sceptre 覆盖数据包在正式世界中，不在 Numen 仓库。

## 4. 验证结果

| 验证 | 结果 |
|---|---|
| Python unittest | 123 通过 |
| numen-api :common:test | 160 通过，0 failures/errors/skipped |
| minecraft-numen :common:test | 267 通过，0 failures/errors/skipped |
| 基线合计 | 550 通过 |
| 仓库外 Python 缺陷探针 | 8 个场景全部复现预期缺陷 |
| Java 真实协议类并发探针 | 相同动作键调用 actuator 2 次 |
| Java 同步 transfer 重试探针 | 相同动作键顺序调用 actuator 2 次 |
| 镜像与仓库源码 SHA 核对 | 544 文件，0 差异 |

测试通过只说明已覆盖路径没有回归，不能掩盖下面的新边界缺陷。探针使用临时目录、模拟 actuator，不调用付费模型或生产 Minecraft。

## 5. 确认缺陷与优先级

下列行号均为原仓库 **1-based**；Serena 返回 0-based，已转换。

### F01 — P1：取消事件会重新进入任务执行

位置：`tools/numen-ab-lab/active_agent.py:226–283`。

cancel 清 checkpoint 后继续向下执行；`TriggerPolicy` 对 cancel 返回真，普通 `triggered` 未排除 cancel，也未丢弃取消之前的 chat。同批 `chat→cancel` 会再次 `_auto_turn`，仅 cancel 也会调用模型。探针分别复现这两种情况。**不能保证取消后不会再动。**

最小修法：按时间顺序消费事件，cancel 建立任务 generation/fence，过滤之前的任务指令，纯 cancel 收束并推进 cursor，不再启动普通回合；保留 cancel 之后真正的新聊天。回归覆盖“chat→cancel”“cancel→新 chat”“只有 cancel”以及运行中取消。

### F02 — P1：幂等的 lookup→dispatch→record 非原子

位置：`ServerMcpServer.java:353–385`、`IdempotentDispatchRegistry.java:15–51`（目录 `components/numen-api/common/src/main/java/com/dwinovo/numen/server/`）。

HTTP worker 上先 lookup，再等待主线程 invoke，最后 record；两个 worker 可同时未命中。真实 `ServerMcpServer.invokeTool` + barrier fake actuator 实测：同一 client_action_id 返回 t1/t2，**actuator 调用 2 次**。注册表又是非线程安全 LinkedHashMap，其“全部在主线程”注释与真实调用点不符。

最小修法：在受保护的单一派发边界进行 reservation→dispatch→commit，重复请求等待/返回同一个 future/完整回执，失败清理遵守未知副作用原则。加并发、网络丢回执、超时后迟到执行测试。

### F03 — P1：同步世界操作没有幂等记录

位置：`ServerMcpServer.java:379–383`，`TransferTool.java:46–50`，`ContainerTools.java:28–69`。

注册表只在结果含 task_id 时登记。transfer 直接同步修改容器后回 success/message，无 task_id。即使完全顺序地重发相同键也执行两次，独立探针实测 **2 次**。checkpoint 处于 tool_planned 时自动重发这种动作，会重复转移物品。

最小修法：所有副作用工具都保存完整回执，不只 task_id；恢复时先查持久化派发状态。测试 transfer 成功但回执丢失时库存只能改变一次。

### F04 — P1：半写 JSONL 尾巴会吞掉下一次完整提交

位置：`persistent_brain.py:56–86,109–138`。

_load 容忍最后一行损坏，但不修复文件边界。文件末尾为无换行半截 JSON 后，append_batch 直接拼接完整记录；重新打开时整行被当作损坏尾巴忽略。实测追加 hello/reply 后重新读取 **0 消息**。后续再次追加也可能使损坏行不再是最后一行，启动直接失败。

最小修法：锁内扫描最后完整字节边界，备份并截断半写尾部，再追加；不要默默丢失完整事务。测试断电各写入位置、UTF-8 截断、恢复后二次追加。

### F05 — P2：缓存 Context 后会话事务与世界记忆可能陈旧

位置：`active_agent_cli.py:24–47`、`persistent_brain.py:47–86,109–157`、`brain_server.py:110–136`、`world_memory.py:19–72`。

Context 复用修复了初始化开销，但 store 只在构造时加载。另一个 CLI/WebUI 进程在相同进程锁下顺序完成一个回合后，旧 Context 的 `_transactions` 与历史仍陈旧。实测已提交同一 turn 的 committing checkpoint 又提交一次，文件从 2 消息变 **4 消息**。世界记忆同样可能被旧缓存覆盖。

最小修法：每次取得进程锁后从磁盘重建/reload会话和记忆，避免事务缓存参与跨进程真实性判断；reload 必须清空旧缓存再读取。测试两进程交错、摘要、记忆顺序更新。

### F06 — P2：恢复“已提交/提交中”结果没有 final，Context 直接 KeyError

位置：`action_brain.py:549–560`、`brain_server.py:130–136`。

Runtime 返回 `{state:already_committed}` 或 `{state:committed_during_recovery}`，Context 却固定 `result['final']`。独立实测 **KeyError('final')**。checkpoint 可能已删，应用仍认为恢复失败；玩家收不到正常收尾。

最小修法：统一 typed recovery result，允许无新 final 的已提交恢复，不重复发送历史回复。测试两个特殊恢复状态经过 Context/ActiveAgent 的完整链。

### F07 — P2：“不可用工具”early_final 未提交、未清 checkpoint

位置：`action_brain.py:405–413`，对比普通 final 与 empty cap 的 `_commit`。

模型连续调用三个未知工具后直接 return，不 `_commit`。先做一次真实感知再触发该分支，实测返回 early_final 但 checkpoint 仍存在、会话消息 0。下一轮会再次恢复旧任务，可能重复汇报。

最小修法：所有 terminal 分支共用提交/清理收尾；测试 early_final 后下一轮不恢复。

### F08 — P2：首次空事件缓冲后的第一条指令被跳过

位置：`active_agent.py:199–211`。

首次运行且 events=[] 时未建立“已观察过”的状态；后来第一条新 chat 到达仍按“首次历史”只建立 cursor。探针先空轮询再发消息，实测 **run_turn=0**。

最小修法：记录启动水位/epoch，区分首次历史与启动后新事件，不能依赖 cursor 文件缺失。测试全新安装、重启清空缓冲、第一条消息。

### F09 — P2：首次模型请求失败会永久消费玩家命令

位置：`active_agent.py:282–311,413–424`。

在任何工具/checkpoint 创建之前 provider 失败，finally 仍推进 cursor、清 inbox；主循环又静默吞异常。实测下一轮不再尝试，cursor 已指向失败消息、inbox 0、checkpoint 不存在。过去用这种方式抑制重放，但过度牺牲了真实输入。

最小修法：建立 durable inbox/待处理事务，区分尚未开始副作用的可重试错误和 dispatch_unknown；退避与最大重试后显式失败回执，而不是静默抹掉输入。

### F10 — P2：task_stop 的 task_id 形同虚设

位置：`ServerNumenActuatorImpl.java:359–377`、`ServerMcpServer.java:343–347`。

协议接受 exact task_id，actuator 没有核对 ID，永远 stopActive。旧客户端延迟的“停止 t1”可能把新任务 t2 停掉。源码确认，未在真实世界停止新任务来证明。

最小修法：提供 task_id 时必须匹配当前 retained task 或返回已结束/不匹配，不影响新任务；测试 t1 结束、t2 开始后迟到 stop(t1)。

### F11 — P2：max_action_distance 是未接线的安全设置

位置：`SecurityPolicy.java:69`、`ServerControlConfig.java:140`。

Serena 全代码搜索只有 getter 和配置解析处读取，没有 invoke/工具/路径约束读它。因此配置宣称的行动距离限制没有实际执行。

最小修法：明确限制目标距离、搜索半径还是施工路径影响范围，按 schema 类型在派发与实际动作处执行；数值要 finite/非负。测试边界、坐标数组、NaN 与无坐标矿物扫描。

### F12 — P2：普通新回合没有消息来源玩家

位置：`persistent_action_cli.py:236–244`。

attach_events 只写 kind/detail，忽略 source；只有 checkpoint guidance 的 `_guidance_from_events` 保存来源。之前“模型知道谁说的”仅适用于中途插话，普通新任务不成立。

最小修法：每条事件携带来源玩家、目标、event_id/time；保持数据与指令分离。测试两位玩家、Console、新回合与 guidance 一致性。

### F13 — P2：成功轮询仍隐含一次 list_companions

位置：`CompanionContext.list_recent_events`、`ServerMcpServer.java:407–430`。

虽然 Context 已缓存，工具参数仍传名字；服务端每次 resolveCompanion(name) 都调用 listCompanions。降噪不等于消除调用。之前“每轮仅一次请求”的表述需纠正：一个 HTTP 工具调用内部仍有枚举 + 查询。

最小修法：UUID 绑定后外部工具使用 UUID；显示/触发仍用名字。缓存需侦测同名重新创建、UUID 变更。通过 fake roster 调用计数回归。

## 6. 附加部署缺陷：此前性能数据包不是完全等价

正式世界覆盖文件：`world/datapacks/yachiyo-commandsceptre-performance-fix/data/commandsceptre/functions/explosion_on_ground.mcfunction`。

旧语义：先筛 tag + OnGround，再 limit=1。覆盖语义：先按 tag limit=1，再检查 OnGround。存在 A 在空中、B 已落地时，旧函数能点燃 B，新函数可能一直选择 A，B 暂不被触发。仓库外纯逻辑反例结果 `equivalent=false`。

**此前“功能不变”的保证不成立；单个 tick 线程采样也不足以证明它是卡顿主因。**本轮不继续未经实测改变生产包。建议先在隔离世界构造多个 tagged 实体验证，再选择保留原语义的候选筛选策略，或回滚覆盖并通过 Spark/JFR 实测成本。不能拿用户确认 Numen 对话正常来证明第三方数据包功能无回归。

## 7. 风险/架构限制（不要混同已复现缺陷）

1. **运行中插话并非实时**：run_once→model/tool polling 是同步阻塞链，最长任务等待 300s；期间不拉新事件。cancel 服务端能停身体，但若模型仍继续决策，下一动作可能重新派发。这个限制此前已记录，本轮进一步确认，不是新发现。需事件 pump + generation fence + 决策/派发边界读取取消。
2. **幂等命中绕过权限/租约/参数验证**：只以 companion+key 查表，不绑定 principal/tool/args。协议探针另一 principal+另一 tool 也得到旧 task，actuator 调用 0。实际泄露取决于攻击者能否知道键；不能宣称无凭据外部攻击。需授权后查询并绑定请求 fingerprint。
3. **注册表非持久化/有界**：服务器重启或128条淘汰后旧 key 消失，planned checkpoint 不具跨重启 exactly-once 保证；同步 transfer 更明显。
4. **未知任务误写成功**：`_poll_existing_task` 对 unknown task_id 直接构造 done/cleared after completion，可能是重启、淘汰或未完成，不应等同成功。现阶段由模型感知兜底并非强制验证。
5. **授权前恢复 dormant 身体**：actuator.invoke 和 ExecuteToolPayload.handle 先 respawn 再授权。知道他人 UUID 的已认证请求可能造成不该发生的唤醒；需在 owner/policy 检查之后恢复。
6. **旧可写 WebUI 安全边界**：BrainServer 可绑定任意 host，token 可为空，未限制 Content-Length、连接超时和线程数；不要公网暴露。token URL/localStorage 也是泄漏面。纯本地 monitor 与此不是同一服务。
7. **世界记忆无 dimension**：多维度下同坐标不同位置会冲突；当前主链一般限制 overworld，但多服务器/多维度扩展需加维度键。
8. **依赖可重复构建不足**：Gradle SNAPSHOT、Forge 插件范围及固定版本重复发布 API 可能造成源码/嵌入产物不同。锁定依赖与 jarJar class 验证应进入发布门禁。未运行完整在线 CVE 数据库审计，不声称依赖无漏洞。

## 8. 推荐修复顺序

1. P1：cancel 时间顺序 + generation fence；MCP 幂等原子化、同步动作回执持久化；半写 JSONL 修复。
2. P2：Context store 刷新、统一恢复结果、所有 terminal 收尾、durable inbox 重试。
3. 运行契约：task_stop 精确 ID、距离策略接线、来源身份、UUID 调用。
4. 隔离服：网络丢回执、进程终止/服务器重启、300s任务中插话/取消、容器转移仅一次。
5. 单独评估第三方数据包回归；正式 JAR 发布必须隔离验收后由 Wayne 重启。

本轮没有发现能够据此直接声称“公开无凭据远程执行命令”的漏洞；也没有证明项目已无 bug。报告是当前 HEAD 的证据化问题清单，不是生产修复完成证明。
