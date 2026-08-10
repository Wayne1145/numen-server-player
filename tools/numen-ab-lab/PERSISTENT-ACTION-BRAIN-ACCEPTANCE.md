# Numen 持久行动大脑 MVP：隔离验收报告

## 结论

已把“每同伴独立持久会话”接入真实 Numen MCP 行动循环，并在隔离 Mohist 1.20.1 服务器完成跨进程联合验收。

这不是只会记聊天内容的脚本。当前 MVP 已能：

- 从实时 `list_companions` 按完整名字绑定唯一 companion UUID；
- 使用 `server_id + companion UUID` 作为会话、恢复点和进程锁的长期身份；
- 将用户请求、模型工具决策、紧凑工具结果与最终答复作为一个完整回合提交；
- 模型思考期间不持有身体 Lease；
- 写动作只在派发时使用短租约；
- 异步动作按原 `task_id` 等待 `done/failed/timeout/stopped`；
- 明确拒绝把 `idle` 当成功；
- 在进程中断后根据 checkpoint 恢复，而不是静默重放工具；
- 对模型非法 JSON 输出进行有限纠错重试，且不把纠错噪声写入长期历史；
- 用每 UUID 非阻塞文件锁阻止两个大脑并发操作同一长期会话。

## 主要文件

- `action_brain.py`：行动回合、短租约、精确任务终态、checkpoint 与恢复。
- `persistent_brain.py`：JSONL 会话、单记录批次事务、`turn_id` 幂等提交。
- `persistent_action_cli.py`：实时 MCP + DeepSeek 路由的可执行入口。
- `test_action_brain.py`
- `test_persistent_brain.py`
- `test_persistent_action_cli.py`
- `test_brain_runner.py`

## 事务与恢复语义

### 正常回合

1. 读取已提交历史；
2. 用户请求只进入内存中的临时回合；
3. 模型生成工具决策；
4. 动作前写 `tool_planned` checkpoint；
5. 短租约派发动作；
6. 收到动作回执后立即写 `tool_dispatched + task_id`；
7. 按精确 ID 等待结构化终态；
8. 写 `tool_completed`；
9. 模型生成最终答复；
10. 写 `committing` checkpoint；
11. 以单条 JSONL 记录提交整个回合，记录 `turn_id`；
12. 删除 checkpoint。

### 崩溃恢复

- `tool_dispatched + task_id`：只查询同一任务，不重新派发工具；
- `tool_completed`：直接从已取得的工具结果继续问模型；
- `committing`：按 `turn_id` 幂等补提交；
- 历史中已经存在 `turn_id`：只清理残留 checkpoint；
- `tool_planned` 尚无派发回执：状态模糊，拒绝自动重放，需要人工判断；
- 动作调用已经到达服务器、但进程在收到 HTTP 回执和写入 task_id 之间退出，仍属于无法凭客户端单独消除的极窄模糊窗口。当前策略是宁可停住，也不重复副作用。后续可通过服务端 idempotency key / client_action_id 完全关闭这个窗口。

## 长期历史格式

新行动回合使用一条 JSONL 记录：

```json
{
  "transaction_id": "turn UUID",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "tool decision JSON"},
    {"role": "user", "content": "<tool_result ...>...</tool_result>"},
    {"role": "assistant", "content": "final"}
  ]
}
```

每条 JSONL 就是一个完整事务，避免断电后留下语法正确但语义残缺的半回合。读取器同时兼容旧的一行一消息格式。

工具轮询的完整 `samples` 保留在当前运行结果中供审计，但不再写入长期历史；长期历史只保留：

- 工具名；
- dispatch；
- task_id；
- terminal。

这样保留结构化事实，同时避免大量重复 `running` 样本快速挤满上下文。

## 自动化测试

最终：**35/35 通过**。

覆盖：

- UUID 精确绑定与歧义拒绝；
- 每 UUID 进程锁；
- 短租约顺序；
- 精确 task_id 轮询；
- `idle` 拒绝；
- dispatch hook 在轮询前记录 task_id；
- 完整回合单事务提交；
- `turn_id` 幂等；
- 模型调用失败不写半回合；
- 非法 JSON 有限重试；
- 纠错噪声不进入长期历史；
- checkpoint 防静默重放；
- 已派发任务恢复时只查询原 ID；
- 已提交回合残留 checkpoint 的幂等清理；
- 模糊 `tool_planned` 拒绝自动恢复；
- 原 A/B 短租约、评分与持久记忆测试无回归。

## 真实隔离验收

### 身份

- 同伴：`ABBrain`
- UUID：`4875e14c-525d-46f3-89bb-5a30801fa3d9`
- 会话：`sessions/numen-ab/4875e14c-525d-46f3-89bb-5a30801fa3d9.jsonl`

### 进程 A：记忆 + 行动

用户请求：记住识别词 `星潮-824`，走到 `x=10.5,z=0.5`，然后读取身体状态验证。

真实轨迹：

```text
goto → task_status(t7) running... → done → get_self_status → final
```

结果：

- `t7` 明确返回 `done`；
- 终态最终位置 `x=10.25695,y=100,z=0.5`；
- 随后身体状态为 `x=10.58976,y=100,z=0.5`；
- 模型按 1.5 格停止容差判定成功，没有重复 `goto`；
- 历史从 0 增至 6 条；
- 进程正常退出。

### 进程 B：跨进程回忆 + 只读状态

全新进程加载同一 UUID 的 6 条历史，请求不要移动、复述识别词并读取当前坐标。

- 模型先调用一次 `get_self_status`；
- 得到位置 `x=10.58976,y=100,z=0.5`；
- 最终答复阶段首次返回非法格式；
- 运行时没有移动、没有重放写动作，保留 `tool_completed` checkpoint；
- 加入格式纠错后，用另一个全新进程执行 `--recover`；
- 恢复进程没有再次调用 `goto` 或 `get_self_status`；
- 直接使用 checkpoint 中的已完成结果；
- 正确复述 `星潮-824`；
- 历史从 6 增至 10 条；
- checkpoint 清除。

- 第二个真实只读回合使用最终运行时完成：加载 10 条历史，调用一次 `get_self_status`，历史增至 14 条；新事务包含 4 条消息且不含 `samples`；checkpoint 在提交后清除；UUID 进程锁路径已实际启用。

最终回复：

```text
生命值 20/20（满血），坐标 x≈10.59、y=100、z=0.5，状态正常。
```

这同时证明了：

1. 跨进程长期记忆；
2. 真实身体行动；
3. 结构化终态进入下一轮推理；
4. 工具完成后模型失败的恢复；
5. 恢复不会重复动作。

## 隔离状态

验收时：

- 正式服 PID `10500`：`21294 / 25567`；
- 隔离服 PID `15584`：`25584 / 25585 / 25586`。

正式服未部署、未停止、未重启、未修改。

## 当前明确边界

MVP 尚不等于原版 Numen 完整长期大脑。仍缺少：

- 上下文自动压缩与长期摘要；
- persona/角色卡槽；
- Markdown skills 与按需 `load_skill`；
- WorkBlockMemory / 世界重要方块记忆；
- 入站聊天监听与事件 inbox；
- 主动生存事件、死亡/重生事件进入大脑；
- 常驻 daemon/API 服务与多服务器调度；
- 服务端 `client_action_id` 幂等派发；
- Web UI、工具时间线与可选客户端 UI。

## 建议下一阶段

先做“长期认知层”而不是立即做 UI：

1. 受预算约束的上下文压缩；
2. persona + skills；
3. 结构化世界记忆；
4. 事件 inbox；
5. 服务端动作幂等键；
6. 再包装成常驻服务和 Web UI。

这样每个同伴不只会记住一句口令，而会逐渐拥有自己的工作方式、熟悉的地点，以及从失败中留下来的路径。
