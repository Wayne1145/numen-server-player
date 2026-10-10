# Minecraft 邻近版本适配：实验构建边界

## 结论与发布矩阵

| MC / 加载器 | 结果 | 游戏内实测 |
|---|---|---|
| 1.20.1 / Forge | 主线，完整任务/取消/插话/恢复隔离验收已完成 | 是；尚非所有模组/场景全面通过 |
| **1.20 / Forge 46.0.14** | **实验 JAR 编译、167 API＋267核心单元测试、封装门禁通过** | **否；未启动1.20服务器、未验证入服或任务行为** |
| 1.19.4 / Forge 45.4.5 | 首次API编译出现大量GuiGraphics缺失；停止移植 | 否；不提供JAR |
| 1.20.2 / Forge 48.1.0 | 首次common编译14处报错，涉及真实玩家生命周期/存档/皮肤和GUI；停止移植 | 否；不提供JAR |
| 1.20.3 / 1.20.4 / 1.20.5+ / 1.21+ | 本轮未尝试；不得根据编号接近推断支持 | 否；不提供JAR |
| Fabric、NeoForge、纯Paper/Spigot | 本轮不适配 | 否 |

**只在 Minecraft 1.20.1 完成游戏内验收。**
1.20资产是 build-only 实验移植，不是“已确认兼容”。请先在备份副本测试；不要在正式世界直接安装。Minecraft 1.20不是1.20.1，两个JAR不可互换；本实验JAR限定 `[1.20]` 和 Forge `[46,47)`，不会借宽松metadata假装覆盖其他版本。

## 为什么有的能小改，有的不能

Numen创建真实ServerPlayer，依赖MC内部玩家、连接、存档、容器与Mixin接口，不是只调用稳定插件API的聊天插件。内容变化少不代表Java二进制/生命周期兼容。

### 1.20：小改可编译

- 按真实1.20 mappings/Forge重新编译核心和内嵌引擎，不复用1.20.1二进制。
- 唯一Java适配：`SimpleButton.renderWidget` 从 protected 改为 public，满足1.20父类接口。
- 构建设置修改：版本/精确范围、Forge46、匹配目标的引擎坐标与独立实验版本号；不配置Fabric模块，避免解析未移植的Fabric依赖。
- 不修改主线1.20.1业务逻辑。构建脚本从固定Git ref导出副本并应用唯一锚点补丁。
- 版本号：核心 `0.0.11-mc1.20-exp.1`；内嵌引擎 `0.0.7-fix4-mc1.20-exp.1`。

### 1.19.4：停止

当前源码依赖1.20引入的GuiGraphics接口；HUD、聊天界面、按钮/输入框等大量编译失败。虽然服务端不显示GUI，现有共用源码工程仍包含这些类，不能随意删目录而不处理共同引用与Mixin边界。需要渲染兼容层或完整服务端源码分离，不符合本轮“小范围修改”条件。

### 1.20.2：停止

真实编译错误包括：
- ServerPlayer构造需要ClientInformation。
- PlayerList.placeNewPlayer需要CommonListenerCookie，涉及新增连接阶段。
- DimensionDataStorage使用SavedData.Factory新契约。
- MinecraftSessionService/GameProfile属性接口变化。
- 皮肤、人物预览、鼠标滚轮等客户端接口改变。

这不只是14行替换：需要同时审计假人入服/空连接/存档/Mixin与Forge网络桥接，然后运行专用服务器验收。common已失败，尚未进入Forge编译，14处不是完整迁移工作量。按用户“不大改”的边界，本轮没有继续改，也不发布伪适配JAR。

## 可复现构建

从仓库根，Python 3.11+、Git、Java17工具链、可访问MC/Forge Maven网络：

```bash
# 从固定提交导出独立工程；脚本只接受已批准的1.20目标。
python3 tools/build_forge_port.py --target 1.20 \
  --ref HEAD --output /path/to/new-empty-port-directory --build
```

Windows/WSL下output使用Windows本地盘可减少文件IO延迟。Windows用`C:/...`，WSL用`/mnt/c/...`；路径必须不存在，脚本不会覆盖现有工程。Linux/macOS用本地绝对路径。

工具执行API common:test/publish + forge:publish，再执行核心common:test + forge:build。工程带匹配版本内嵌API，发布给使用者只需外层`-all.jar`，**不要再单独装numen_api引擎**。

本机Forge环境需要额外代理/证书环境检查参数，属于本机网络绕过，未写为默认用户配置。`--index`仅用于发布前验证当前暂存树，正式复现请用固定Git提交。跨版本脚本回归与其他Python测试均通过：156项。

## 产物门禁与SHA

- 主/嵌套JAR metadata均为MC `[1.20]`、Forge `[46,47)`、displayTest `IGNORE_SERVER_VERSION`。
- 主/嵌套manifest均记录Built-On-Minecraft 1.20。
- 嵌入API与本次1.20发布源逐字节相同。
- API167、核心267单测无failures/errors/skipped；这不是游戏内验证。
- 外层`numen-forge-1.20-0.0.11-mc1.20-exp.1-all.jar` SHA256：`b6fbf275bfaa242ff9bce6fc932e10094ec00f474cbb604573cbc8a5e975bcc1`。
- 内嵌API SHA256：`463ddd7117123c7532c6d347020d394044a50750b1e444ca47a869c6e9aebe28`。

发布为GitHub **pre-release**，不替换现有稳定release。附构建证据和SHA256SUMS。证据文件 `docs/ports/1.20-build-evidence.json`。

## 顺带修复干净源码缺失

原 `.gitignore` 的token通配规则排除了 `AuthTokens.java` 与 `AuthTokensTest.java`；本机有源码而Git导出缺类。加入精确白名单并提交两份不含真实密钥的源码。新的导出回归强制检查鉴权类存在，避免再发布只能本机构建的工程。

## 下一步验收清单（尚未执行）

1. 纯Forge1.20服务器启动无Mixin/类加载异常；客户端不装mod也能进入。
2. 新建同伴、正常入服/离线/存档重启、皮肤与实体可见性。
3. 只读MCP、短移动、采集→容器→熔炼链。
4. 同步幂等、模型等待插话/取消、跨重启恢复。
5. 通过后再评估同版本混合端，不能把Forge编译当作Mohist已验证。
