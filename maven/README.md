# maven/

Fork 引擎的公共 Maven 产物（raw maven 布局，Gradle 可直接作为 maven 仓库）。

坐标：`com.dwinovo.numen:numen-api-common-1.20.1:0.0.7-fix3`（及 `numen-api-forge-1.20.1`）

Maven 仓库 URL（`multiloader-common.gradle` 的 `numenRemote` 已指向这里）：

```
https://raw.githubusercontent.com/Wayne1145/numen-server-player/main/maven
```

## 更新方法

引擎代码有改动时：

```bash
cd components/numen-api
./gradlew :common:publish :forge:publish \
  -Plocal_maven_url="$(realpath ../../local-maven)" --no-daemon --console=plain

# 同步到公共 maven 目录（只保留当前发布版本，历史版本可删）
cp -r ../../local-maven/com maven/com
# 更新 maven/com/dwinovo/numen/*/maven-metadata.xml 的版本列表
git add maven && git commit && git push
```

注意：`local-maven/` 目录本身被 gitignore（本地发布缓存）；`maven/` 目录是**要提交**的公共产物。
