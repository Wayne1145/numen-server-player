# Contributing

感谢你愿意改善 Numen Server Player。

## 开始之前

1. 阅读 `README.md`、`NOTICE.md` 和 `docs/ROADMAP.md`。
2. 不要把本项目描述成官方 Numen。
3. 保留上游版权与 SPDX 头。
4. 核心派生实现使用 `LGPL-3.0-only`；只有明确位于 `com.dwinovo.numen.api` 的公共集成 API 使用 MIT。
5. 不要提交上游 All Rights Reserved 图像、音频、Logo 或皮肤。

## 开发原则

- 所有外部控制必须经 `ServerNumenActuator`，不得增加绕过策略、Lease 或审计的旁路。
- Minecraft 世界操作必须调度到服务器主线程。
- 新的控制入口必须具有鉴权、输入上限、超时和取消语义。
- API Key、Bearer Token、服务器地址、世界存档和玩家数据不得进入 Git。
- 专用服务器代码不得急加载客户端类。
- 功能完成不等于“无崩溃”；必须验证可观察结果。

## 提交 Pull Request

请在 PR 中说明：

- 修改目的与风险；
- 涉及 Forge、Mohist 或哪些模组；
- 执行过的测试命令；
- 是否改变权限、Lease、任务或网络边界；
- 若修复兼容性问题，附上经过脱敏的最小日志。

建议提交前运行：

```bash
cd components/numen-api
./gradlew :common:test :common:build :forge:build --no-daemon --console=plain

cd ../minecraft-numen
./gradlew :common:test :common:build :forge:build --no-daemon --console=plain
```

如果修改了 MCP，请同时运行独立协议客户端或等价集成测试。

## 报告问题

Issue 中请提供：

- Minecraft、Forge/Mohist、Java 版本；
- 最小模组列表；
- 可复现步骤；
- 预期与实际结果；
- 脱敏后的日志片段。

请移除 Token、API Key、公网 IP、域名、玩家 UUID、世界坐标和私人目录。
