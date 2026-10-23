---
title: 配置
description: 配置语料、工作空间、embedding provider、共享源码策略和进程环境。
---

Codalith 使用两个 TOML 文件。`${VAR}` 表示必需的环境变量，`${VAR:-default}` 提供默认值。相对路径始终相对于声明它的 TOML 文件解析，不依赖进程的当前工作目录。

## 语料注册表

`configs/registry.toml` 包含 `schema_version`、`default_target`、一个或多个 `[[corpora]]`，以及可选的 `[[workspaces]]`。

```toml
schema_version = 2
default_target = "${CODALITH_DEFAULT_TARGET:-sample}"

[[corpora]]
id = "sample"
display_name = "Sample"
description = "Small local corpus"
revision = "sample-v1"
source_root = "../fixtures/sample_corpus"
index_root = "${CODALITH_SAMPLE_INDEX_ROOT:-../data/indexes/sample}"
adapter = "python"
embedding_provider = "fake"
include_extensions = [".py"]
exclude_globs = [".git/**", ".venv/**", "**/__pycache__/**"]

[[workspaces]]
id = "all"
corpora = ["sample"]
```

`adapter` 使用 `codalith.languages.catalog` 中的 ID：`python`、`csharp`、`cpp-ue` 或 `generic`。`embedding_provider` 为 `fake`、`fastembed` 或 `openai`。示例使用的 `fake` 用于确定性演示，不代表真实模型的语义能力。

`semantic_store` 仅供 `--semantic adopt` 使用；`--semantic build` 始终将 embedding 写入新 generation。被采用的 store 必须记录与配置 provider 相同的模型和维度。采用后，其绝对路径固定在 generation manifest 中，所以后续修改 `semantic_store` 仅影响以后的构建。

`include_extensions` 使用后缀匹配，支持 `.Build.cs` 和 `.Target.cs`。空列表表示由 adapter 决定支持哪些文件。

`exclude_globs` 对规范化后的语料相对路径进行不区分大小写的匹配。包含 `/` 的模式以语料根目录为起点，除非以 `**/` 开头。因此 `Templates/**` 仅排除根目录下的 `Templates`，而 `**/ThirdParty/**` 在任意深度生效。不包含 `/` 的模式（如 `node_modules`）匹配任意深度的单个路径段。

## 源码策略

`configs/source-policy.toml` 定义默认及硬性行数预算、最大源码字节数，以及至少一个 deny glob。

模式遵循相同规则，在建索引时、每次查询输出时和读取源码时均进行不区分大小写的检查。`**/.env` 同时匹配根目录和嵌套目录的文件。路径穿越、绝对路径、空路径段和点路径段在 glob 匹配前即被拒绝。

Deny globs 和字节上限属于 generation 的源码选择哈希。更改这些值会使现有 generation 失效，需要重建；与此同时，被拒绝的路径也不会出现在结果中。

## 环境变量

`.env.example` 包含供 Compose 使用的配置，主要分组如下：

- `CODALITH_HTTP_*`：监听地址、端口、endpoint、额外允许的 Host/Origin 和请求体上限。
- `CODALITH_EMBEDDING_*`：OpenAI 兼容语料所需的 endpoint 和密钥。
- `LANCE_INCLUDE_VECTOR_CENTROIDS=false`：避免加载大型 IVF centroid 统计。
- `CODALITH_UE_*_HOST_DIR`：UE 源码、结构索引和 semantic store 的宿主机绑定挂载。
- `CODALITH_UE_EMBEDDING_*`：被采用的 UE store 应具有的来源信息。

Compose 自行设置容器内 registry、source policy 和监听 host。直接运行本地命令时，`CODALITH_REGISTRY` 与 `CODALITH_SOURCE_POLICY` 选择 TOML 文件，`CODALITH_DASHBOARD_DIR` 指定预先构建的面板资源目录。

运行命令不会修改 `.env` 或客户端配置。显式运行的 Agent 安装器仅合并所选 MCP 配置和技能路径，并保留备份，详见[安装](../installation/)。Docker Compose 会加载 `.env`；直接本地命令需要在进程环境中提供相关变量。

不要提交 `.env`、挂载的源码、生成索引、报告或凭证。
