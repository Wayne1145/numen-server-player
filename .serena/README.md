# Serena 入口说明

## 真源与镜像

- 真源：`/mnt/d/numen-server-player`。
- 索引镜像：`/home/wayne/.hermes/workspace/numen-serena-source`。
- 更新镜像：`python3 /mnt/d/numen-server-player/.serena/sync-source-mirror.py`。
- 索引：`/home/wayne/.local/bin/serena project index /home/wayne/.hermes/workspace/numen-serena-source`。
- 按需 MCP：`/home/wayne/.local/bin/serena start-mcp-server --project /home/wayne/.hermes/workspace/numen-serena-source --context ide --enable-web-dashboard false --open-web-dashboard false`。

镜像排除 Gradle 构建配置发现，避免在 WSL 自动导入 Windows Forge 工程；Java 使用源码符号导航，完整类型/跨模块引用仍须原工程构建与文本检查补证。镜像不代表第二个可编辑真源，任何修改回到真源实施，再重新同步。

源文件索引：521 Java + 23 Python。`source-manifest.json` 保存真源 HEAD 和每个源文件 SHA-256。新增/修改源码后同步并索引；不可使用旧镜像下结论。

## 项目记忆

- `memories/architecture/core.md`：架构与运行路径。
- `memories/audit/open-findings.md`：审计发现及未修状态。
- `../docs/serena-audit-2026-10-10.md`：正式审计报告。

缓存不提交，项目记忆与配置可按需要提交。没有修改全局 serena-hud 项目或 Hermes MCP 注册。
