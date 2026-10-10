# Numen 0.0.11：审计修复与隔离验收

## 状态

审计起点 `ce6ea14`。本次修复 Java 身体/派发端与 Python 常驻大脑，候选核心 `0.0.11`、内嵌 API `0.0.7-fix4`。以下是**隔离验收**，不是正式服已加载声明。原始审计报告保留历史事实，当前状态以本文件为准。

## 修复映射

| 原发现 | 修复 | 自动回归/行为证据 |
|---|---|---|
| F01 取消重触发 | 最新取消事件建立时间边界；模型等待泵事件；持久 generation 围栏拒绝迟到动作 | cancel 批次、迟到决策测试；隔离等待期取消约 2.07 秒退出，库存不变 |
| F02 并发重复派发 | 授权后主线程 journal：预留→执行→完整回执 | 并发 goto 两次请求得到同一 t1 |
| F03 同步操作重复 | 同步 transfer 保存完整回执；键绑定身份、工具、规范参数 | 实际库存 10→7+3，重试仍7+3；异参数同键拒绝；JVM重启后仍7+3 |
| F04 半写 JSONL | 文件锁内备份坏尾并截断，保留中段损坏报错 | UTF-8 截断与重新追加回归 |
| F05 陈旧 Context/store | 取得锁后 reload；事务去重在文件锁内；世界记忆写锁 | 陈旧 store 同事务仅提交一次 |
| F06 恢复返回契约 | 特殊恢复结果允许空 final，不重复回话 | Context 恢复结果回归 |
| F07 early-final 遗留 | unavailable-tool 收束正常 commit/clear | early-final 提交回归 |
| F08 空启动漏首指令 | 持久初始化空水位 | 首指令回归 |
| F09 首次失败丢指令 | durable inbox 与接收水位分离；持久退避 | provider 失败输入保留、退避回归 |
| F10 task_stop 无目标约束 | 精确任务 ID 比较；迟到 stop 不影响新任务 | 隔离迟到 stop 后当前任务仍 running |
| F11 距离策略未接线 | 参数半径/目标校验与 tick 移动围栏 | 超距目标拒绝；策略/移动围栏单测 |
| F12 来源身份丢失 | 新回合事件序列保留 kind/source/time/detail | Console 与玩家来源回归 |
| F13 名字隐含枚举 | Context 绑定 UUID，读/写工具使用 UUID | 调用契约回归 |
| 幂等跨身份命中、授权前复活 | 授权/策略/租约检查在恢复身体与 journal 命中之前 | 授权接线与 journal fingerprint 回归 |
| unknown task 假成功 | error + requires_verification，不伪造 done | unknown retained task 回归 |
| WebUI 边界 | Bearer-only compare_digest；空 token 禁止非回环启动；body/连接限制；sessionStorage | HTTP/鉴权测试 |
| 记忆维度、路径 | 世界记忆加入 dimension；技能/人设名不得越目录 | 维度与 traversal 回归 |
| 构建漂移 | ForgeGradle、Loom 版本固定；发布 SHA 门禁；公共 Maven 同步全部模块 | jarJar 与公共 forge API 逐字节相同 |
| 恢复竞态遗漏 | RecoveryRequired 竞态分支不再连续3次失败就删除快照 | 新增 RED→GREEN，4次失败后原 turn 快照仍在 |

## 最终自动门禁

- Python `python3 -m unittest discover -s tools/numen-ab-lab -q`：**154 通过**。部分旧 fake Context 和临时目录测试仍打印预期 warning，不能称日志完全无警告。
- numen-api common JUnit XML：**167 通过，0 failures/errors/skipped**。
- minecraft-numen common JUnit XML：**267 通过，0 failures/errors/skipped**。
- 引擎 common/forge/fabric 发布成功；核心 clean common:test + forge:build 成功。
- `tools/verify_release.py`：嵌入 API 与公共发布源完整字节一致。

## 真实隔离行为

全部在独立 Forge 1.20.1 测试服进行，未接入正式大脑，也未修改正式世界。

1. **并发幂等**：同键两次 goto 均返回 t1；不是两个动作。
2. **同步转移**：固定槽36/37，石头10个→7/3；重试回执相同，库存不再变化；同键不同 count 返回 conflict。
3. **控制台聊天闭环**：`/numen say Actor` → `source=Console` → SenseNova 真实决策 → craft → 状态核对 → 游戏回话；1白桦原木消耗、4木板入背包，checkpoint为空。
4. **运行中插话**：真实模型请求前添加可观测等待窗口，期间 `/numen say` 插入“月读验收”；后续模型输入与最终回复都包含口令，原任务未新建。测试只增加延迟，不伪造模型答案。
5. **模型等待取消**：RCON `execute as Actor run numen cancel Actor` 走真实玩家命令路径；代数1→2，约2.07秒逻辑退出；等待迟到模型返回后库存仍不变；旧代数 craft 被拒绝。逻辑取消不代表物理取消上游HTTP请求。
6. **身体动作取消**：真实 goto t8 排队后执行玩家取消，下一tick retained 终态为 stopped。取消后立即读取可能短暂 unknown；未知不算成功，需节流重读并感知。
7. **真实生存采铁→熔炼**：和平难度、固定小型矿石/熔炉夹具，背包只有铁镐、煤、食物；真实模型18步工具链挖出1粗铁，加载熔炉和煤，取出1铁锭并保留背包；原矿块挖掉，炉子为空，checkpoint为空。**不等于复杂 Mohist 地形、268模组或敌对生存链全面通过。**
8. **跨进程恢复**：真实合成完成后主动断开模型决策链，保留 `tool_completed` 与 turn `756258bd-62cd-4e3d-9cca-e33cff2c1984`；新Python进程恢复并核验4木板，没有再次消耗原木，checkpoint收尾。
9. **JVM重启持久性**：煤转移10→7+3后正常停服、重启；代数仍4，同动作键回执相同，库存仍7+3。

行为证据 JSON 位于本目录 `audit-0.0.11-evidence/`。仅包含脱敏结果，不提交环境密钥、HTTP Authorization 或服务器配置。

## 构件

- 核心 SHA-256：`390533564c9adf14328d87bcf9f5e4a78f025a5f00287ae02852529730f0da52`
- 内嵌 forge API SHA-256：`0d2ff661f2061d5e3b6c80c19840b511cc58b03a72804adcfdf1df57c5e707d3`

## 清理与正式部署边界

- 隔离服已正常停机。测试世界独立归档；世界恢复到验收前快照，策略恢复到原始字节内容，临时 DESTRUCTIVE 权限撤销；候选JAR留在隔离 mods。
- 正式服 JAR、Python运行副本、systemd服务、世界数据包本轮未改。**当前正式服务仍是旧实现，源码提交不使其生效。**
- 第三方 Command Sceptre 原性能覆盖非等价，仓库提供 `tools/numen-ab-lab/datapacks/commandsceptre-equivalence` 原语义回滚包；正式服旧覆盖尚未回滚。部署时必须备份旧覆盖并单独决定恢复原函数，不宣称正式问题已消失。
- 后续正式验收：部署 Java与Python同版本 → 用户重启JVM → 核对JVM启动时间/磁盘SHA → 原生玩家 `/numen say`、插话、取消、背包核对。

## 未作承诺

未完成长期 soak、多玩家高并发、完整依赖CVE审计、Fabric运行与每一种模组容器协议验收。journal满10000条失败关闭，不自动淘汰；需要带审计的归档策略。距离约束是参数与tick围栏，不是完整物理/插件传送拦截。WebUI保持本机服务，不因本次修复而适合公网暴露。取消期间后台HTTP线程仍可能等到超时返回。
