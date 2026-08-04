# Numen Server Player

[![Minecraft](https://img.shields.io/badge/Minecraft-1.20.1-62B47A)](https://www.minecraft.net/)
[![Forge](https://img.shields.io/badge/Forge-47.x-DFA86A)](https://files.minecraftforge.net/)
[![Mohist](https://img.shields.io/badge/Mohist-tested-8A77D5)](https://mohistmc.com/)
[![License](https://img.shields.io/badge/code-LGPL--3.0--only-blue)](LICENSE)

把 Numen 扩展成一个**纯服务端、真实 `ServerPlayer`、可由 MCP 或可选模型 API 控制的 Minecraft 玩家**。

它不是站在原地执行几条命令的 Worker，也不是 ArmorStand 外壳。伴生体拥有服务器原生玩家身份、物理、背包、拾取、容器、死亡、复活和 playerdata，并通过同一套安全执行层接受控制。

> 当前目标：Minecraft 1.20.1、Forge 47.x、Mohist 1.20.1。无需客户端安装本模组。项目仍处于生产接入前的工程验证阶段，暂未发布 Release。

## 核心能力

- **真实服务器玩家**：`NumenPlayer extends ServerPlayer`
- **纯服务端运行**：无需玩家客户端安装对应模组
- **MCP-only 可用**：没有任何模型 API Key 时，外部 MCP 客户端仍能完整控制
- **可选 API Agent**：兼容 OpenAI 风格 API；与 MCP 共用执行器和 Lease
- **统一执行路径**：所有动作都进入 `ServerNumenActuator`
- **控制租约**：principal、TTL、fencing token，避免双重控制
- **策略与审计**：能力白名单、维度/命名空间限制、速率限制、动作日志
- **异步任务**：`task_id`、状态轮询、取消、死亡/卸载/关服清理
- **原生交互**：移动、拾取、背包、箱子、死亡与重启恢复均有真实服务器验收证据

## 架构

```text
可选 OpenAI-compatible API Agent ─┐
                                  ├─ ServerNumenActuator
外部 MCP Client / Hermes ─────────┘   │
                                      ├─ Auth / Policy / Rate Limit
                                      ├─ Lease / TTL / Fencing
                                      ├─ Audit / Cancellation
                                      ↓
                              Minecraft Server Thread
                                      ↓
                         NumenPlayer / TaskQueue / Tools
                                      ↓
                         原生 ServerPlayer 世界行为
```

MCP 和 API Agent 不是两套旁路。它们最终使用相同的动作、权限、Lease 和审计语义。

## 已验证范围

| 环境/能力 | 状态 |
|---|---|
| 空白 Forge 1.20.1 | verified |
| 空白 Mohist `b562929a` / Forge 47.4.0 | verified |
| 267 模组配置副本 + 全新世界 | 核心功能 verified，运行参数 needs-adaptation |
| MCP 鉴权、握手、能力过滤 | verified |
| 真玩家移动与任务取消 | verified |
| 原生拾取与 `ChestMenu` | verified |
| 死亡 `live → dormant → live` | verified |
| companion 身份跨重启恢复 | verified（窄范围） |
| API 与 MCP 同时竞争一个身体 | 待专项验收 |
| 正式世界/正式服长期运行 | 未完成 |

完整阶段说明见 [`docs/reports/`](docs/reports/)；路线图见 [`docs/ROADMAP.md`](docs/ROADMAP.md)。公开报告经过脱敏，不包含世界、整合包或服务器凭据。

## 仓库结构

```text
.
├── components/
│   ├── numen-api/          # ServerPlayer 引擎、Actuator、MCP、API Agent
│   └── minecraft-numen/    # 工具、任务、寻路与行为实现
├── docs/
│   ├── ROADMAP.md
│   └── reports/            # 可公开的阶段验收摘要
├── scripts/
│   ├── mcp-acceptance-client.sh
│   └── minecraft-rcon.py
├── LICENSE                 # LGPL-3.0-only（顶层及核心代码）
├── LICENSE-API             # com.dwinovo.numen.api 的 MIT 许可
├── LICENSE-ASSETS          # 上游资产边界说明
└── NOTICE.md               # 上游归属、修改范围与来源
```

## 构建

### 前置条件

- JDK 17（构建 Forge 1.20.1）
- Git
- 可访问 Forge/Minecraft Maven 仓库的网络
- 建议至少 4 GiB 可用构建内存

### Linux / WSL

```bash
# 1. 构建并发布引擎到仓库内本地 Maven 目录
cd components/numen-api
./gradlew :common:build :common:publish :forge:build :forge:publish \
  -Plocal_maven_url="$(realpath ../../local-maven)" \
  --no-daemon --console=plain

# 2. 构建最终 Forge 模组
cd ../minecraft-numen
./gradlew :common:build :forge:build \
  -Plocal_maven_url="$(realpath ../../local-maven)" \
  --no-daemon --console=plain
```

最终 Forge JAR 位于：

```text
components/minecraft-numen/forge/build/libs/
```

当前服务端分支只承诺 Forge/Mohist；Fabric 不属于这个公开快照的验收目标。

## 服务端配置原则

首次启动会创建 Numen 服务端控制配置。建议：

1. MCP 仅监听 `127.0.0.1`，由本机客户端或安全隧道访问；
2. 使用至少 256-bit 随机 Bearer Token；
3. 不把 Token 放在 URL query string；
4. 模型 API Key 使用环境变量或被忽略的服务器本地文件；
5. 不把 API Key 写入主配置、日志或版本控制；
6. 初次部署先关闭破坏性能力，只启用只读与身体动作；
7. 正式世界测试前先做完整备份，并在副本环境验证相同模组/config。

可使用独立客户端做 MCP 验收：

```bash
bash scripts/mcp-acceptance-client.sh \
  http://127.0.0.1:25567/mcp \
  '<你的随机测试 Token>' \
  McpBot
```

## 当前限制

- 还未完成长期 soak、多玩家高并发与正式世界压力验收；
- 并非所有模组专属 GUI/自定义网络协议都已适配；
- Mohist 在机械盘和大型整合包下可能需要较长的保存/关服窗口；
- Mohist 可能派生额外 JVM，内存约束必须保证父子进程都能继承；
- 不应在无备份、无隔离验证的正式世界中直接试装。

## 开源与上游归属

本仓库是 [Dwinovo/numen-api](https://github.com/Dwinovo/numen-api) 与 [Dwinovo/minecraft-numen](https://github.com/Dwinovo/minecraft-numen) 的服务端派生项目，不是官方 Numen 发行版。

- 核心源码：**LGPL-3.0-only**
- `com.dwinovo.numen.api` 公共集成 API：**MIT**
- Numen 原始美术、品牌和音频：**All Rights Reserved**

为遵守资产许可，本公开快照不分发上游 ARR 图像/音频资产。详细来源与提交点见 [`NOTICE.md`](NOTICE.md)。

## 贡献

欢迎提交 Issue 和 Pull Request。开始前请阅读：

- [`CONTRIBUTING.md`](CONTRIBUTING.md)
- [`SECURITY.md`](SECURITY.md)
- [`NOTICE.md`](NOTICE.md)

请勿在 Issue 中上传世界存档、服务器地址、Token、API Key、玩家数据或完整日志；先完成脱敏。
