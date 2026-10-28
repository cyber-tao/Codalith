---
title: 配置
description: 配置目录语料、共享 Git 数据库、管理 profile、embedding provider 与源码策略。
---

Codalith 使用两个 TOML 文件保存静态配置。`${VAR}` 表示必需的环境变量，`${VAR:-default}` 提供默认值。相对路径始终相对于声明它的 TOML 文件解析，不依赖进程的当前工作目录。托管仓库、数据库、构建计划和任务保存在持久 SQLite 目录中；已发布 Git 版本从共享池注册，不反写 TOML。

## 语料注册表

`configs/registry.toml` 包含 `schema_version`、`default_target`、目录型 `[[corpora]]`、可选的 `[[workspaces]]` 和 `[management]`。当 `management.enabled = true` 时，可以用空 corpus 列表与空默认目标启动，再通过面板创建第一个数据库。

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

<a id="shared-git-databases-and-management"></a>

## 共享 Git 数据库与管理配置

此功能属于当前源码检出。需要继续提供目录型 generation 查询时，保留已有 `[[corpora]]`；管理 profile 也可以独立声明，无需先创建目录语料：

```toml
schema_version = 2
default_target = ""

[management]
enabled = true
storage_root = "../data/management"
source_roots = ["/srv/sources"]
admin_token_env = "CODALITH_ADMIN_TOKEN"
max_selected_tags = 100

[[management.profiles]]
id = "ue-shared"
label = "Unreal Engine source"
adapter = "cpp-ue"
embedding_provider = "openai"
embedding_model = "text-embedding-3-small"
include_extensions = [".h", ".hpp", ".inl", ".cpp", ".c", ".cs", ".usf", ".ush"]
exclude_globs = ["**/ThirdParty/**", "**/Intermediate/**", "**/Binaries/**"]
```

将 `/srv/sources` 替换为服务器上已有的源码根目录。Windows TOML 路径可以使用正斜杠，例如 `D:/Sources`。只能浏览和注册这些根目录内的仓库。容器中将源仓库只读挂载，将管理存储目录可写挂载；浏览器目录选择不会上传客户端电脑上的源码树。

启动服务前，在 `admin_token_env` 指定的进程环境变量中设置私有管理员 token，长度**至少 16 个字符**；默认变量名为 `CODALITH_ADMIN_TOKEN`。不要把 token 值写入 TOML、URL、项目绑定或 MCP 客户端配置。面板连接表单仅将它保存在页面内存，刷新后清除。全部 `/api/admin/*` 接口要求此 bearer token。管理默认关闭，启用时必须同时配置 token 和至少一个源码根目录。

Profile 固定数据库的 adapter、扩展名、排除路径和 embedding provider/model。已有静态 corpus 也作为可选 profile；新 profile ID 必须与其保持唯一。显式 management profile 与目录 corpus 不同，必须提供非空 `include_extensions`。源码策略的 deny 和字节上限继续叠加生效。

OpenAI 兼容 profile 使用服务／worker 进程环境中的 `CODALITH_EMBEDDING_BASE_URL` 和 `CODALITH_EMBEDDING_API_KEY`；profile 不保存凭证值。导入旧向量时选择兼容模型，不同 provider/model/dimension/input 配置进入独立语义命名空间。

| 环境变量 | 含义 |
| --- | --- |
| `CODALITH_MANAGEMENT_ENABLED` | `true`/`false` 或 `1`/`0`，覆盖 `enabled`。 |
| `CODALITH_MANAGEMENT_STORAGE_ROOT` | 覆盖共享存储根目录。 |
| `CODALITH_MANAGEMENT_SOURCE_ROOTS` | 覆盖允许的源码根目录，使用系统路径分隔符连接：Windows 为 `;`，Linux/macOS 为 `:`。 |
| `CODALITH_ADMIN_TOKEN` | 默认 secret 变量；可通过 `admin_token_env` 更换变量名。 |

空语料注册表需要在 TOML 中声明 `enabled = true`，不能仅依赖环境变量覆盖。面板与 CLI 流程见[运维](../operations/#shared-database-workflow)。

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

服务和建索引命令不会修改 `.env` 或客户端配置。显式 Agent 安装器及 `project bind` 仅更新所选 MCP 配置与项目绑定，并保留备份，详见[安装](../installation/)和[项目版本绑定](../operations/#project-version-binding)。Docker Compose 会加载 `.env`；直接本地命令需要在进程环境中提供相关变量。

不要提交 `.env`、挂载的源码、生成索引、报告或凭证。
