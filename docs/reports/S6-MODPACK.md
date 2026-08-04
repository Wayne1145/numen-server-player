# S6-B 验收报告：private modpack模组/config 副本 + 全新世界

## 结论

**阶段判定：needs-adaptation。**

Numen 已在目标 Mohist `1.20.1-b562929a`、正式服模组/config 的只读副本以及全新世界上完成真实整合包运行验证。核心功能——MCP、真实 ServerPlayer、Lease、异步移动、任务取消、原生拾取、ChestMenu、死亡恢复与重启持久化——均为 `verified`。

尚需适配的是运行参数和运维窗口，而不是 Numen 行为：Mohist 会派生/重启 JVM，在当前并行运行两台其他 Java/Minecraft 服务的内存压力下，默认/较大堆参数会触发 native allocation failure；隔离进程改为可继承的 `JAVA_TOOL_OPTIONS=-Xms512M -Xmx2500M -XX:+UseSerialGC -XX:MaxDirectMemorySize=384M` 后稳定启动。D: 是承载 WSL 的机械盘，关服需要长窗口；结构化持久证据中的第二次关服耗时 89.009 秒并自然退出。

## 隔离与固定环境

| 项目 | 值 |
|---|---|
| 运行目录 | `<isolated-server-dir>` |
| 世界 | `s6b_fresh_world`（全新生成） |
| Minecraft / Mohist / Forge | 1.20.1 / b562929a / 47.4.0 |
| Mohist SHA-256 | `933514c5ff8df47ab8fdb106ad435945bf6512702f9299cc66c4d173a1b70620` |
| Numen SHA-256 | `27179d0df10f42d464e596b2db9e4b648bf99ca6572f83fea43c3e2ea952c986` |
| 源快照 | `mods/config/kubejs/defaultconfigs/scripts` |
| 源文件数 | 2650（准备时快照） |
| 原模组 JAR | 267；加入 Numen 后 `mods/` 共 269 个文件 |
| 游戏 / MCP / RCON | 25582 / 25581 / 25583，全部仅回环监听 |

准备脚本不复制正式世界；`level-name` 明确指向新的 `s6b_fresh_world`。正式服只作为复制源。`production_written=false` 是脚本执行结果，但未对正式目录做前后全量哈希/USN 审计，因此严谨表述为：**本次流程未执行或发现正式服写操作；未建立覆盖整个正式目录的法证级“不变”证明。**

## 验收矩阵

| 项目 | 状态 | 原始证据 |
|---|---|---|
| 267 个正式模组 + 配置加载 | verified | 启动日志到 Mohist READY，FTB Quests 1193 项、KubeJS/目标模组配置加载 |
| Numen MCP 启动 | verified | `server up on http://127.0.0.1:25581/mcp` |
| MCP 认证/握手/能力过滤 | verified | `16-s6b-mcp-client.log` 全部 PASS |
| 真实身体 `goto` | verified | `(4,0) → (11,4)`，任务到 idle |
| Lease 获取/释放 | verified | 外部 MCP 客户端完整 acquire/release |
| 任务取消 | verified | `running → task_stop → idle` |
| 原生拾取 | verified | `minecraft:cobblestone x3` 进入真实背包 |
| 原生箱子菜单 | verified | `GUI: ChestMenu`，slot 0 `apple x5` |
| 死亡恢复 | verified | 同 companion ID `live → dormant → live`，约 33 秒恢复，HP 20/20 |
| 重启持久化 | verified（窄范围） | 同 companion ID 跨重启恢复为 live，身体健康；不声称任务中间态/完整库存均持久化 |
| D: 正常关服 | verified-with-long-timeout | `save-all flush` 成功；持久证据显示第二次 `stop` 89.009 秒自然退出、未强杀 |
| 默认 JVM 参数 | needs-adaptation | 两次 native allocation failure；可继承的受限父/子 JVM 参数后通过 |
| 长期稳定性/高并发 | needs-evidence | 本阶段为短时新世界验收，不替代长期 soak test |
| API/MCP 同时争抢同一身体 | pending | 共享 Lease 路径已知，但本阶段未做同时竞争专项 |

## 功能原始结果

### MCP 外部客户端

`16-s6b-mcp-client.log` 验证：

- 无 Bearer 与 query token 均为 401；
- initialize/initialized/tools/list 正常；
- destructive `mine` 未暴露；
- companion 创建、Lease、`goto`、轮询、位移确认、释放全部通过。

### 真玩家功能

权威原始 JSON 为 `18-s6b-functional-raw-pass.json`：

- 取消：任务 `t4` 先为 `running`，`task_stop` 返回 `stopped`，随后 `idle`；
- 拾取：RCON 仅创建隔离场景，拾取动作由 MCP/Actuator 执行，背包出现 cobblestone ×3；
- 容器：`interact_at(button=right)` 后返回 `ChestMenu` 与 `apple x5`；
- 死亡：同一 companion UUID 由 dormant 恢复 live，满血且 `body_health.degraded=false`。

`17-s6b-functional-raw.json` 保留首次测试脚本错误：`interact_at` 遗漏必填 `button`，死亡断言使用了错误字段 `health/max_health`；服务端原始状态实际已经完成死亡恢复。两处均是测试客户端适配，不是 Numen/整合包失败。

### 重启持久化

`20-s6b-restart-persistence.json` 验证同一 companion UUID：

```text
6702c3fa-e4fe-4e5a-bb21-0e3c96b40611
```

重启后为 live，HP=MAX HP，`body_health.degraded=false`，新 entity id=1407。

## D 盘机械盘结论修订

the operator 明确 D: 是承载 WSL 的机械盘，较慢属于预期。本阶段证明：

1. D: 能完整加载重度整合包并运行 Numen；
2. `save-all flush` 可成功完成；
3. 第二次结构化持久关服证据为 89.009 秒自然退出；
4. 第一次观测曾在 watchdog/RegionFile 栈后约 14 分 22 秒自然退出，但同名结构化证据被第二次运行覆盖，因此仅作观测背景，不作为精确耐久证据；
5. D: 不再标记为 unsupported。后续标准是：关服前 `save-all flush`，`stop` 后至少等待 15 分钟，并以监听端口/进程真正消失为成功条件。

## 已知基线与范围

启动日志仍包含正式整合包已知或模组自身警告，例如 KubeJS/recipe、缺失可选 mixin 目标、客户端类 dedicated-server 警告等。本阶段没有把这些归因给 Numen。

本阶段没有覆盖：

- 正式世界副本；
- 长时间 soak、高玩家并发和大规模区块探索；
- API/MCP 同时争抢同一 companion；
- Refined Storage/Tom's Storage/TACZ 等每个模组专属 GUI/网络协议的逐项操作。核心原生容器/背包/生命周期已通过，但不能由此推导所有模组 GUI 均已支持。

## 阶段边界

S6-B 到此停止。下一阶段 S6-C 只能在明确继续后进行：复制**正式世界的副本**到 D/F 隔离环境，绝不直接启动或写入正式世界。
