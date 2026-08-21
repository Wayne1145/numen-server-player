# Numen 打断语义方案：`/numen cancel` 清 checkpoint + 新指令优先插入

> 状态：待审（未改任何代码）
> 日期：2026-08-21
> 提出人：Wayne（打断语义要求） · 整理：八千代

## 1. 目标语义（Wayne 的要求）

玩家在游戏里对 yachiyo 说话、或执行 `/numen cancel` 时，期望：

1. **打断模型的操作** —— 身体当前任务立即停止（现状已有：`stopActive`）
2. **打断模型的 checkpoint** —— 模型"之前发出的东西"（未提交的工具调用、transient 状态）**全部失效**，不再自动恢复旧回合
3. **保留对话历史** —— 已提交进会话 JSONL 的历史回合保留，不删除
4. **新消息 = 指引/插入** —— 如果模型正在处理旧回合，玩家再发的新消息应立即生效，作为新指引插入，而不是被 checkpoint 挡在门外排队

一句话：**"玩家点名"永远优先于"旧回合自动恢复"。**

---

## 2. 现状（代码级确认，2026-08-21 实读）

| 环节 | 现状 |
|---|---|
| 服务端 `/numen cancel` | 只调 `CompanionTickDispatcher.stopActive(companion, "stopped by command")` 停身体任务；**不写任何事件**，大脑完全感知不到"被叫停" |
| 大脑 `run_once` 优先级 | **checkpoint 优先**：`pending.path.exists()` → 先 `recover_turn()` 恢复旧回合，成功则直接返回，**根本不看新事件** |
| checkpoint 生命周期 | 工具派发时写（`tool_planned`/`tool_completed`）；回合 commit 成功才清；恢复失败 3 次才清 |
| 新 chat 事件 | 仅在**无 checkpoint** 时才被处理成新回合；有 checkpoint 时被晾在一边 |
| 对话历史 | 已提交历史（`store.append_batch`）保留；checkpoint 内未提交状态（transcript/工具链）会被恢复 |

**由此产生的痛点**（本次会话实测复现）：
- checkpoint 卡在 `drop_items`（丢铁锭）上，模型恢复后**继续丢**，玩家新指令不生效
- 模型 429 恢复失败时，checkpoint 反复尝试恢复，玩家说什么都被挡

---

## 3. 方案设计

### 3.1 服务端改动（JAR，numen-server-player）

**文件**：`components/minecraft-numen/common/src/main/java/com/dwinovo/numen/core/debug/DebugCommands.java`

**改动**：`cancel()` 在 `stopActive` 之后，追加写一条事件：

```java
// cancel 后通知外部大脑：旧回合作废，不要再自动恢复
com.dwinovo.numen.core.tools.RecentEventsTool.BUFFER.add(
        "cancel", companion.getName().getString(),
        "@" + companion.getName().getString() + " cancel");
```

- `kind="cancel"` 与 `chat`/`death` 并列，大脑按 kind 识别
- `detail` 带 `@名字` 保证触发策略（mention_words）可命中
- 事件写于 `stopActive` **之后**，保证大脑看到 cancel 时身体任务已停（顺序安全）

### 3.2 大脑侧改动（Python，numen-ab-lab）

**文件**：`active_agent.py`（`run_once` 优先级重排）+ `active_agent.py`（TriggerPolicy 扩展）

**核心：优先级从"checkpoint 优先"改为"玩家指令优先"**

```
run_once:
    1. events = list_recent_events(); new_events = 游标后的事件
    2. 若 new_events 含 kind==cancel 事件：
         checkpoint.clear()        # 旧回合作废（未提交状态丢弃）
         日志: "玩家已 cancel，旧 checkpoint 作废"
    3. 若 new_events 含玩家点名 chat 事件（mention 命中）：
         若 checkpoint 存在: checkpoint.clear()   # 新指令优先，旧回合作废
         开新回合（run_turn，base=已提交历史 + 新消息）   # 指引插入
         return
    4. 否则若 checkpoint 存在:
         恢复 checkpoint（现有逻辑不变）
    5. 否则: 无事
```

**关键点**：
- **保留历史**：`checkpoint.clear()` 只删 checkpoint 文件，不碰 `store`（会话 JSONL 已提交历史），满足"保留对话历史"
- **新消息插入语义**：新回合 `base = store.messages()`（含全部历史）+ `transient = [新消息]`，模型看到"完整历史 + 新指引"，即插入效果
- **cancel 与 chat 分离**：cancel 事件本身只清 checkpoint 不开回合（避免误触发）；真正的新回合由后续 chat 触发
- **TriggerPolicy 扩展**：`kind==cancel` 也视为"值得处理"的事件（否则会被无关事件过滤掉）

### 3.3 边界情况处理

| 场景 | 行为 |
|---|---|
| cancel 后没有新 chat | checkpoint 已清，大脑空闲，等玩家指示（合理） |
| 一批事件同时含 cancel + chat | 先清 checkpoint，再用 chat 开新回合（一轮内完成） |
| 闲聊（点名但非任务） | 同样走新回合（现有语义：闲聊回话）；是否打断长任务可做成配置 |
| 纯 death 事件 | 不打断 checkpoint（避免误清长任务） |
| cancel 时任务已在跑 | 服务端先 `stopActive` 再写事件，身体任务已停，清 checkpoint 无副作用 |
| 模型 429 恢复失败循环 | 新指令/cancel 直接清 checkpoint，不再等 3 次失败 |

### 3.4 不做的事（明确边界）

- **不删对话历史**：session JSONL 永不因 cancel/新指令被删除
- **不自动清已完成回合**：只清"未提交的 checkpoint"
- **不改变崩溃恢复机制**：进程意外崩溃（非玩家干预）仍走原 checkpoint 恢复；玩家干预（cancel/点名）才优先作废

---

## 4. 验证方案

1. **单元测试**（Python 侧）：
   - cancel 事件 → checkpoint 被清
   - 新 chat + 存在 checkpoint → checkpoint 清 + 新回合开
   - 无事件 + checkpoint → 恢复 checkpoint（原逻辑不变）
   - 历史保留断言：清 checkpoint 后 `store.messages()` 不变
2. **隔离服集成测试**（`C:\NumenLab\forge-light`，端口 25566/25570）：
   - 让 yachiyo 开始长任务（如挖 15 铁）→ 中途 `/numen cancel` → 确认身体停 + 大脑 checkpoint 清
   - 再 `/numen say yachiyo 去砍5个木头` → 确认新回合立即执行（不被旧任务卡住）
3. **正式服验收**：隔离服全绿后，部署新 JAR（服务端改动），Wayne 重启正式服验证

---

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 新指令优先可能误清"快完成"的 checkpoint（如挖到 14/15 铁时玩家闲聊） | 可做成配置：仅"明确任务指令"打断，纯闲聊排队；默认先按"点名即打断"实现，体验后再调 |
| JAR 改动需正式服重启 | 隔离服验证充分后再部署，重启由 Wayne 操作 |
| cancel 事件与 stopActive 竞态 | 服务端顺序保证（先停后写）；大脑侧看到 cancel 即认为已停 |
| 事件缓冲容量（200）导致 cancel 被冲掉 | 大脑 15 秒轮询远快于缓冲填满，风险极低 |

---

## 6. 改动清单

| 文件 | 改动 | 生效方式 |
|---|---|---|
| `DebugCommands.java`（服务端） | cancel 追加 `BUFFER.add("cancel", ...)` | 需重新打包 JAR + 部署重启 |
| `active_agent.py`（大脑） | `run_once` 优先级重排 + TriggerPolicy 支持 cancel | Python 直接生效，重启服务即可 |

预计工作量：服务端 ~10 行；大脑侧 ~30 行 + 2 个单元测试。全部改动前先在隔离服验证。

---

*审阅人：Wayne · 待确认项：①新指令是否一律打断 checkpoint（还是仅任务指令）②cancel 语义是否要单独做成命令提醒（游戏内回执已有）*
