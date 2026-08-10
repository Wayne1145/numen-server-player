# Numen 任务终态反馈：隔离服验收报告

## 结论

候选 Forge Jar 已在隔离 Mohist 1.20.1 服务器通过真实 MCP 验收。

- `task_status(companion, task_id)` 可返回并保留结构化终态；
- 真实验收覆盖 `done`、`failed`、`stopped`；
- `timeout` 已由 Java 单元测试覆盖状态映射，本轮未通过篡改游戏时钟或内部状态伪造真实超时；
- 同一终态按原 `task_id` 重复查询，返回内容保持一致；
- 无 `task_id` 查询仍提供当前活动任务的 `queued/running/idle` 快照；
- `task_status` 与 `task_stop` 的重复 MCP Schema 已消除；
- `send_chat` 在隔离服真实调用成功；
- 原始 `scan_area` 未打包、未暴露；
- 正式服未部署、未重启、未修改。

## 候选产物

- 文件：`D:\numen-server-player\components\minecraft-numen\forge\build\libs\numen-forge-1.20.1-0.0.10-all.jar`
- 大小：1,401,942 字节
- SHA-256：`878352206C1BE1D76CEE890D135115EEA69784A5AAA42C32CE656F9A5F9B4A51`
- `ChatTool.class`：存在
- `ScanAreaTool.class`：不存在
- `displayTest = "IGNORE_SERVER_VERSION"`：保留
- `SimpleChannel` 注册：源码搜索未发现

## 构建与自动化测试

1. `numen-api` 服务端测试子集：通过。
2. 修改后的 `numen-api`：已发布至本地 Maven 仓库。
3. `minecraft-numen :common:build :forge:build`：通过。
4. Python A/B 与持久会话测试：16/16 通过。
5. 全量 `numen-api` 仍有 5 个既有语音相关失败，不属于本轮终态反馈改动；不能宣称整个仓库全绿。

## 隔离环境

- 根目录：`C:\numen-ab-lab`
- 游戏：`127.0.0.1:25584`
- MCP：`127.0.0.1:25585/mcp`
- RCON：`127.0.0.1:25586`
- 当前隔离 PID：`15584`

正式环境仍由另一个进程监听：

- PID：`10500`
- 游戏：`21294`
- MCP：`25567`

## MCP 工具清单

最终候选实测：

- 工具总数：34
- `task_status`：1
- `task_stop`：1
- `send_chat`：1
- `scan_area`：0
- `task_status` Schema：含可选 `task_id`

初次候选曾出现两个 `task_status` 和两个 `task_stop`：管理层与通用 `ToolRegistry` 重复暴露同名工具。虽然调用路由命中新实现，但重复 Schema 可能误导模型与通用 MCP 客户端。现已在引擎工具枚举阶段跳过 MCP 管理层保留名称，并重新构建、重新部署、重新验收。

## 真实任务证据

完整 JSON：`/home/wayne/.hermes/workspace/numen-ab-lab/results/terminal-result-live-acceptance.json`

### 成功 `done`

- 任务：`t4`
- 活动态：`queued`
- 终态：`done`
- 结构化结果包含：`success=true`、到达消息、`final_x/final_y/final_z/ground_y`
- 重复查询：与首次终态完全一致

### 主动停止 `stopped`

- 任务：`t5`
- 终态：`stopped`
- 结构化结果包含：`success=false`、`interrupted=true`
- 重复查询：与首次终态完全一致

### 执行失败 `failed`

- 任务：`t6`
- 场景：请求寻找不存在的 `minecraft:definitely_missing_block`
- 终态：`failed`
- 结构化结果包含具体错误原因和最终坐标
- 重复查询：与首次终态完全一致

### 超时 `timeout`

`ExternalTaskResultStoreTest` 已验证 `TIMEOUT → timeout` 和原始 `TaskResult` 保留。真实导航最多允许约 5 分钟检查点预算，并在正常进展时续约。本轮没有为了凑齐状态矩阵而篡改游戏时钟、内部 deadline 或生产代码，因此没有宣称真实隔离服已触发 timeout。

## send_chat

隔离服真实调用返回：

```json
{
  "success": true,
  "message": "said: [Numen 隔离验收] send_chat 工作正常"
}
```

它仍是低层出站聊天原语，不代表已经具备入站监听、角色对话循环或持久聊天大脑。

## 未执行事项

- 没有部署到正式服；
- 没有停止或重启正式服；
- 没有 commit；
- 没有 push；
- 没有重新引入 `scan_area`；
- 没有声称未安装客户端时可获得原版自定义 UI。
