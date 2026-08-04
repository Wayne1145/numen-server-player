# Numen Server Player 使用教程

本教程对应 **MC 1.20.1 / Forge 47.x / Mohist 1.20.1** 的纯服务端 AI 玩家模组。

- 安装目标：服务器 `mods/` 目录，玩家客户端**不需要**安装本模组。
- 运行方式：伴生体是服务器原生 `ServerPlayer`，拥有真实物理、背包、拾取、容器、死亡与存档行为。
- 控制方式：MCP（推荐，无需任何模型 API Key）或可选 OpenAI 兼容 API Agent，二者共用同一安全执行层。

---

## 1. 安装

1. 从 [GitHub Releases](https://github.com/Wayne1145/numen-server-player/releases) 下载 pre-release 的
   `numen-forge-1.20.1-0.0.10-all.jar`。
2. 校验哈希（以 Release 页面标注为准）：
   ```bash
   sha256sum numen-forge-1.20.1-0.0.10-all.jar
   # 应输出 27179d0df10f42d464e596b2db9e4b648bf99ca6572f83fea43c3e2ea952c986
   ```
3. 放入服务器 `mods/` 目录。
4. 重启服务器（**建议先备份世界**，并在副本环境先验证）。
5. 检查日志，看到以下内容说明加载成功：
   ```text
   Numen mod initialised on Forge.
   [numen-task] scheduler heartbeat online (first server tick)
   ```

> 内存提示：Mohist 会派生额外的 JVM。如果在机械盘或内存受限机器上，建议通过
> `JAVA_TOOL_OPTIONS` 同时约束父、子 JVM，例如：
> `-Xms512M -Xmx3G -XX:MaxDirectMemorySize=512M`。

## 2. 首次启动的默认行为（安全默认）

没有 `config/numen/server_control.json` 时，模组使用**最保守默认值**：

- MCP 端点：**关闭**；
- API Agent：**关闭**；
- 能力策略：只允许 `SERVER_READ_ONLY` 与 `SERVER_BODY_ACTION`；
- 允许维度：`minecraft:overworld`；
- 允许命名空间：`minecraft`。

也就是说：**什么都不配置直接启动是安全的**，不会开放任何网络控制端口。

## 3. 控制台命令（无需配置即可使用）

在服务器控制台（或 op 玩家游戏内）执行，需要权限等级 2：

```text
/numen server summon <名字> [x y z]   召唤一个服务器伴生体（可重复召唤，幂等）
/numen server list                     列出所有服务器伴生体及状态
/numen server status <名字>            查看单个伴生体状态（位置/血量/饥饿/是否降级）
/numen server despawn <名字>           永久移除伴生体（会删除其注册与存档，不可恢复）
```

普通玩家可用：

```text
/numen player summon <名字>            召唤属于自己的伴生体
/numen player despawn <名字>           移除属于自己的伴生体
/numen settings                        打开设置界面（仅客户端）
/numen reset                           清除对话循环（仅客户端）
```

## 4. 启用 MCP（外部程序/大模型客户端控制）

创建文件：

```text
config/numen/server_control.json
```

最小示例（回环 + 高熵 Token）：

```json
{
  "policy": {
    "enabled_capabilities": ["SERVER_READ_ONLY", "SERVER_BODY_ACTION"],
    "allowed_dimensions": ["minecraft:overworld"],
    "allowed_namespaces": ["minecraft"],
    "lease_ttl_ms": 30000,
    "max_calls_per_minute_per_principal": 240,
    "max_calls_per_minute_per_companion": 120
  },
  "principals": [
    {"token": "<把这里替换成随机生成的 Token>", "id": "mcp-admin", "role": "ADMIN"}
  ],
  "mcp": {"enabled": true, "host": "127.0.0.1", "port": 25567},
  "api_agent": {"enabled": false}
}
```

生成随机 Token（至少 256-bit）：

```bash
openssl rand -base64 32
# 或
python3 -c "import secrets;print(secrets.token_urlsafe(32))"
```

**安全要求**：

- 只允许 `127.0.0.1`（loopback）绑定；模组会**拒绝**绑定 `0.0.0.0` / `::`。
- 不要使用 query-string 传 Token；必须放在 `Authorization: Bearer <token>` 头。
- 不要把 Token 写进 Git、聊天、截图或日志。
- 跨机器使用请走 SSH 隧道 / Tailscale / WireGuard，而不是开放公网端口。
- 如果没有配置任何 principal 但启用了 MCP，服务端会生成一个一次性 256-bit admin
  Token 并**打印在日志里**（仅本次会话有效，重启会变）。

## 5. 使用 MCP 客户端

本仓库提供独立验收脚本（纯 curl，不依赖 Minecraft 类路径）：

```bash
bash scripts/mcp-acceptance-client.sh \
  http://127.0.0.1:25567/mcp \
  '<你的 Token>' \
  MyBot
```

脚本会依次执行：

```text
鉴权负例（无 Token → 401）
initialize → notifications/initialized
tools/list（能力过滤）
create_companion → list_companions
acquire_control → goto → task_status 轮询
get_self_status 确认真实位移
release_control
```

通用的 JSON-RPC 调用方式：

```bash
curl -s -X POST http://127.0.0.1:25567/mcp \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

常用工具（通过 `tools/call` 调用）：

| 工具 | 作用 |
|---|---|
| `create_companion` | 创建/激活指定名字的伴生体 |
| `list_companions` | 列出伴生体与状态（live / dormant / dead） |
| `acquire_control` | 获取控制租约（返回 lease_id 与 fencing token） |
| `release_control` | 释放租约 |
| `get_self_status` | 位置、血量、饥饿、背包、装备、身体健康度 |
| `goto` | 异步寻路移动到目标坐标 |
| `task_status` / `task_stop` | 查询任务状态 / 取消任务 |
| `collect_items` | 拾取附近掉落物 |
| `inspect_block` / `interact_at` / `inspect_gui` | 查看方块、交互、读取 GUI |

> 租约机制：同一时间只有一个 principal 能控制一个伴生体；到期（TTL）后其他
> principal 才能接管，fencing token 递增，防止双重控制。

## 6. 可选：启用 API Agent（模型自动决策）

在 `server_control.json` 中启用：

```json
"api_agent": {
  "enabled": true,
  "provider": "openai",
  "model": "your-model-name",
  "base_url": "https://api.example.com/v1",
  "max_turns": 8,
  "request_timeout_seconds": 120
}
```

API Key **不放在配置里**，按以下顺序解析：

1. 环境变量 `NUMEN_API_KEY`（或配置 `api_key_env` 指定其他变量名）；
2. 服务器本地文件 `config/numen/api_key.txt`（首行即 Key，加入 `.gitignore`）。

没有可用 Key 时，API Agent 自动保持关闭，**MCP 不受影响**。

## 7. 伴生体生命周期

```text
/numen server summon MyBot
  → live（真实 ServerPlayer，可被玩家看见/交互）

死亡
  → dead（约 30 秒后自动在原位置附近复活，HP/MAX_HP 恢复）

服务器重启
  → 服务器属伴生体自动恢复 live（playerdata 来自世界存档）

/numen server despawn MyBot
  → 永久移除（删除注册与存档，不会复活）
```

- 伴生体拥有独立 UUID 与 playerdata，随世界备份。
- `get_self_status` 里的 `body_health` 反映移动 tick 健康度；`degraded=true` 表示
  身体因连续 tick 异常被暂停，可在 `last_tick_error` 看到原因。

## 8. 生产环境建议

1. 先备份完整世界。
2. 在 **副本环境**（相同模组列表 + 新世界）验证通过后再上正式服。
3. 正式服先只启用 `SERVER_READ_ONLY` + `SERVER_BODY_ACTION`，不要开放破坏性能力。
4. MCP 只监听 loopback；Token 高熵且轮换。
5. 关服时先 `save-all flush`，再用 RCON/控制台 `stop`，并确认 Java 进程退出。
6. Mohist 整合包在机械盘上关服可能很慢，请给出足够窗口（建议 ≥ 15 分钟）。

## 9. 已知限制（当前 pre-release）

- 并非所有模组专属 GUI / 自定义网络协议都已适配（如部分大型存储、枪械、附属模组需逐项验证）。
- 长时间 soak、多玩家高并发、正式世界压力测试尚未完成。
- API Agent 与 MCP 同时竞争同一个伴生体的专项并发验证尚未完成。
- 正式世界长期运行属于“自行验证”阶段；遇到问题请提供脱敏日志。

## 10. 故障排查

| 现象 | 原因与处理 |
|---|---|
| MCP 请求 401 | Token 错误 / 未配置 principal；检查 `server_control.json` |
| MCP 连接 404 / 拒绝 | MCP 未启用（默认关闭）；检查配置与端口 |
| 日志没有 `[numen-mcp]` | 同上 |
| 伴生体不在线 | 尚未 summon，或已被 despawn |
| `body_health.degraded=true` | 身体 tick 异常被暂停；查看 `last_tick_error` |
| 关服很慢 | 机械盘 RegionFile 写入慢；`save-all flush` 后耐心等待 |
| 日志出现内存错误 | Mohist 双 JVM；用 `JAVA_TOOL_OPTIONS` 约束父、子 JVM 堆上限 |

## 11. 源码与构建

见仓库根目录 `README.md` 的「构建」一节：先构建并发布 `numen-api` 到仓库内
`local-maven`，再构建 `minecraft-numen` 的 Forge 目标，产物在：

```text
components/minecraft-numen/forge/build/libs/
```
