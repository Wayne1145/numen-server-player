# 公共 Maven 构件

本目录是 fork 引擎的公共 raw Maven 仓库，要随源码提交；`components/local-maven/` 只是被忽略的本机构建缓存。

当前版本 `0.0.7-fix4`，包含 common、forge、fabric 三个模块：

- `com.dwinovo.numen:numen-api-common-1.20.1:0.0.7-fix4`
- `com.dwinovo.numen:numen-api-forge-1.20.1:0.0.7-fix4`
- `com.dwinovo.numen:numen-api-fabric-1.20.1:0.0.7-fix4`

公共源：`https://raw.githubusercontent.com/Wayne1145/numen-server-player/main/maven`。
Fabric 构件用于 Gradle 配置阶段依赖解析，不代表 Fabric 运行行为已验收。

## 更新

从仓库根执行：

```bash
# 发布全部被工程配置引用的模块，避免只构建 Forge 时 Fabric 配置阶段缺依赖。
cd components/numen-api
./gradlew :common:publish :forge:publish :fabric:publish --no-daemon --console=plain
cd ../..
# 默认发布路径相对引擎工程根解析，实际为 components/local-maven/。
# 保留历史版本，复制新版本目录与对应 maven-metadata.xml 及校验侧车。
cp -r components/local-maven/com/. maven/com/
# 引擎改变后 clean 重建；门禁校验 jarJar 与本次公共发布源完全一致。
cd components/minecraft-numen
./gradlew clean :common:test :forge:build --no-daemon --console=plain
cd ../..
python3 tools/verify_release.py \
  components/minecraft-numen/forge/build/libs/numen-forge-1.20.1-0.0.11-all.jar \
  maven/com/dwinovo/numen/numen-api-forge-1.20.1/0.0.7-fix4/numen-api-forge-1.20.1-0.0.7-fix4.jar
```

网络代理、JDK 与平台适配属于本机环境；不要把关闭 ForgeGradle 证书环境检查当成默认安全配置。校验 SHA 不能代替依赖漏洞审计。
