# Numen 随服托管大脑（Forge 1.20.1，0.0.12）

## 两种安装方式

- **托管模式 `managed`**：只放一个 `-all.jar` 到 `mods/`。首次启动释放大脑源码和配置；Windows x64 无需另装 Python、pip、WSL 或 systemd。配置模型后，Mod 在服务端启动时创建本机 Python 子进程，正常停服或 JVM 退出时子进程随之退出。
- **外置模式 `external`**：沿用现有独立 Python/systemd/容器运行方式，可把大脑放到另一台机器。停 MC 服不会自动停外置服务。该模式不创建 Mod 托管子进程。

已有 `server_control.json` 的服务器，首次安装本版时默认创建 **external** 配置，绝不抢走现有大脑。全新环境默认 **managed**，但没有模型配置时不会启动大脑或调用收费 API。

> 内嵌 `numen-api` 是玩家身体/MCP引擎，不是 Python 大脑。引擎的可选 Java `api_agent` 也不是这套持久大脑；使用托管模式应禁用 `api_agent`，避免两套模型同时接管。

## 新环境安装

1. 安装匹配的 Minecraft **1.20.1 / Forge 47.x** 服务端。
2. 把 `numen-forge-1.20.1-0.0.12-all.jar` 放到 `mods/`，移走旧版 Numen JAR；不要再独立安装内嵌 API 引擎。
3. 首次启动：自动创建下列文件。服务器正常启动，大脑显示 `setup required`，不调用模型。
4. 正常停服，编辑 `config/numen/managed_brain.json`，填写 `llm_url` 与 `llm_model`。
5. 将自己的 API Key 写入 `config/numen/llm_key.txt`，或使用配置指定的环境变量。不要将密钥放进聊天、命令行、截图或版本库。
6. 再次启动：看到 `[numen-brain] managed process started (pid=...)`。用控制台 `numenctl create MyBot` 创建服务器所有的同伴，再用 `numen say MyBot 你好，报告自己的状态` 对话。游戏内同样可以 `/numen say MyBot ...`。

```json
{
  "mode": "managed",
  "python_executable": "",
  "llm_url": "https://你的服务商/v1/chat/completions",
  "llm_model": "你的模型ID",
  "api_key_env": "NUMEN_LLM_KEY",
  "api_key_file": "llm_key.txt",
  "mcp_principal_id": "managed-brain",
  "companions": "",
  "poll_seconds": 15,
  "cooldown_seconds": 120
}
```

- `llm_url` 是完整 chat completions 接口，不是只有 `/v1` 的基地址。
- `companions` 留空监听所有 live 同伴；指定 `MyBot,OtherBot` 可限定范围。每个同伴按 UUID 独立保存会话。
- `cooldown_seconds` 是自动回合最小间隔，默认120秒；需要更快连续对话可降低。
- 可设置 `python_executable` 为自己 Python **3.10+** 的绝对路径，跳过内置Windows运行时。
- 新环境的 MCP 只绑定 `127.0.0.1`，令牌每服随机生成，自动选择本机端口；若端口后来被占用，可在停服后调整 `server_control.json` 的 `mcp.port`。不开放公网、不修改防火墙。

## 允许正常生存动作

安全默认没有启用挖掘、放置、攻击、丢弃。只读对话/身体动作可以先验收；若服主明确允许自主生存，在停服后向 `server_control.json` 的 `policy` 配置加入：

```json
{
  "enabled_capabilities": ["SERVER_READ_ONLY", "SERVER_BODY_ACTION", "DESTRUCTIVE", "ADMIN_ONLY"],
  "allowed_dimensions": ["minecraft:overworld"],
  "allowed_namespaces": ["minecraft"],
  "max_action_distance": 64
}
```

该对象是 `policy` 的值，不是替换整个控制文件。保留 MCP 与随机 principals。`DESTRUCTIVE` 包含挖掘、放置、攻击和丢弃；`ADMIN_ONLY` 开放同伴管理。多维度或模组物品按实际服务器逐项允许，不自动放开所有权限。

## 平台边界

| 平台 | 自动部署 |
|---|---|
| Windows x64 | 大脑源码和 CPython 3.13.16 官方嵌入式运行时已包含在 JAR；服主机无需联网下载Python或执行安装器 |
| Linux/macOS | 自动释放大脑源码，使用系统 `python3` 3.10+；不提权、不运行 apt/brew，也不改变全局Python。未安装Python时需先安装，或指定 `python_executable` |
| Windows ARM64/32位 | 未打包对应运行时，需配置匹配架构的 `python_executable` |
| 容器/面板限制子进程 | 托管启动可能被拒绝；使用包含Python的镜像或外置模式 |
| Fabric/NeoForge/Paper/Spigot | 本轮未实现该托管入口。纯插件端不能安装Forge Mod |
| MC 1.20 实验版 | 仍为上一版实验构建，不包含本轮托管功能；未擅自扩大支持范围 |

大脑只使用标准库，无 pip 安装。Windows运行时在**构建时**从 Python 官网下载并核验固定 SHA256，发行 JAR 自带全部所需内容。运行时许可见嵌入ZIP的 `LICENSE.txt`。

## 文件与升级

```text
config/numen/
  managed_brain.json       # 模式、模型与启动参数
  server_control.json      # MCP、鉴权和动作权限；含秘密，禁止公开
  llm_key.txt               # 自己的模型密钥
  python-3.13.16/           # Windows x64首次需要启动大脑时释放
  brain/                    # 白名单打包的Python源码与技能
    sessions/managed/       # 持久会话
    checkpoints/managed/    # 尚未提交的回合
    inbox/managed/          # 待消费输入
    cursors/managed/        # 事件接收水位
    memory/managed/         # 世界记忆
  brain-logs/managed.log    # 大脑日志，不混入MC高频日志
```

新 JAR 在启动时更新白名单代码；不覆盖服主配置、Key、会话、checkpoint、世界。不要手改 `brain/*.py` 作为长期配置；代码更新会覆盖源码。应备份整个 `config/numen/` 和服务器世界。

## 生命周期与恢复

- 正常 `stop`：Mod 关闭父子连接，大脑退出；未完成快照保留。**停服不等于 `/numen cancel`**。
- JVM 崩溃/被强制终止：操作系统关闭连接，Python守望线程在连接断开后退出，包括正在等待模型HTTP结果时。不使用只比较PID的脆弱检测。
- 若只是暂停/挂起服务器进程或网络调用缓慢，连接未断，不宣称已停服；上游请求已发出时不能撤销服务商已计费用。
- 下次开服按现有恢复规则核对任务与generation，不盲目重发已发生世界副作用的动作。
- 大脑自身异常退出时本版不自动无限重启；查看独立日志并重启服务器。无子进程权限/运行时缺失时服务器仍能正常启动。
- 模型在云端，停止本地大脑不会停止服务商的模型服务；本地 LM Studio/Ollama 同样不是Mod管理的进程。

## 旧环境迁移

1. 在身体任务空闲时停止旧外置大脑；备份其 sessions/checkpoints/inbox/cursors/memory。
2. 部署新版JAR；保留旧控制配置，初始 `mode=external`。不可同时开两个大脑。
3. 手动选 `mode=managed`，填模型配置，将 `mcp_principal_id` 指向既有控制配置里的正确principal，确认MCP绑定 `127.0.0.1` 且 `api_agent` 关闭。
4. 托管数据目录与旧外置数据不同，不会自动复制/合并。需要保留记忆时先备份，再做server-id与UUID映射；不要把旧checkpoint随意复制后恢复。
5. 重启并进行只读对话验收。正式服重启与迁移必须由服主选择，本轮没有替换正式服。

## 验收记录

本轮在独立的 Windows Forge 1.20.1 / Forge 47.4.0 新世界实际验证：

- 首次启动自动释放大脑、生成配置与本机鉴权，没有模型配置不启动子进程；
- 不依赖系统Python/WSL，内置 CPython 原生启动；真实模型调用 `get_self_status` 并中文回复20/20HP；
- 正常停服：JVM与Python两个PID都退出；
- 强制结束隔离JVM：子进程自动退出；之后再次开服成功；
- 全新同伴创建后立即发首句：修复启动水位后收到一次真实回复；
- Linux与Windows Python各159项测试通过，Java核心271项通过；拒绝ZIP越界、保留既有配置、重复安装保留令牌和模式；
- 本轮没有在Mohist正式服加载此版，也没有声称Linux完整Forge生命周期或长期运行已验收。
