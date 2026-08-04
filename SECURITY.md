# Security Policy

## Supported versions

项目尚未发布稳定 Release。安全修复目前只面向 `main` 最新提交。

## 私下报告安全问题

请不要公开披露以下问题：

- MCP 鉴权绕过或 Token 泄露；
- 绕过 Lease、策略或能力白名单执行动作；
- 路径遍历、任意文件读取/写入；
- 远程代码执行或命令注入；
- API Key/玩家数据/服务器数据泄露；
- 可远程触发的拒绝服务。

请通过 GitHub 的 **Security → Report a vulnerability** 私下提交。如果私有漏洞报告暂不可用，可先创建一个不含细节的普通 Issue，请维护者开启安全沟通渠道。

报告请包含版本、复现条件、影响、最小复现和建议修复。不要提交真实 Token、API Key、世界存档或玩家数据。

## 部署安全基线

- MCP 默认只监听 loopback；
- 使用高熵 Bearer Token；
- 禁止 query-string Token；
- API Key 只来自环境变量或 Git 忽略的本地文件；
- 通过能力白名单逐步开放动作；
- 正式世界部署前完成备份和副本验收；
- 不要把管理端口直接暴露到公网。
