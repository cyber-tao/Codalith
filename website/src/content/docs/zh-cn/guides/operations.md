---
title: 运维
description: 构建多版本源码数据库，管理持久任务，绑定 UE 项目，并运行本地或 Docker 服务。
---

## 本地示例

```bash
uv sync --frozen --extra dev
uv run codalith index build --corpus sample --semantic build
uv run codalith doctor --target sample --deep
uv run codalith serve --transport http
```

`/healthz` 报告进程健康状态。只有默认目标具有语义就绪的 generation 时，`/readyz` 才返回 200。两条路由与 MCP endpoint 使用相同的 Host/Origin allow-list。

stdio 使用 `codalith serve --transport stdio`。`codalith client-config` 打印 Codex 或 Claude 配置，并显式包含 uv 项目目录，使客户端可从任意工作目录启动它。

## Compose 示例

```bash
cp .env.example .env
docker compose up --build mcp-http
docker compose ps
```

`index-sample` 完成后才启动 `mcp-http`。索引保存在 `codalith-data` 数据卷中，每次主动重建发布一个新 generation。镜像包含已构建的运维面板。

<a id="ue-574"></a>

## 目录型 UE 5.7.4

在本地 `.env` 设置：

- `CODALITH_UE_SOURCE_HOST_DIR`：包含 `Engine/` 的 UE 检出根目录。
- `CODALITH_UE_INDEX_HOST_DIR`：可写的结构 generation 目录。
- `CODALITH_UE_SEMANTIC_HOST_DIR`：基于同一 UE 源码构建的现有 semantic store。
- 与该 store 一致的 embedding provider、模型、base URL 和密钥。

采用现有 semantic store 并构建结构 generation：

```bash
docker compose --profile ue run --rm index-ue
docker compose --profile ue up -d mcp-http-ue
uv run codalith client-config --client codex --transport http
```

`adopt` 不重新 embedding，也不写外部 store。它拒绝记录的模型或维度与配置 provider 不一致的 store，拒绝与结构快照内容哈希不一致的文件，记录覆盖率，并在 generation manifest 中固定 store 的绝对路径。因此，容器中采用的 generation 指向容器路径；要在宿主机提供同一 store，应重新 adopt。`--semantic build` 始终在新 generation 自身的 `semantic/` 目录构建，成本较高，可能调用付费 embedding endpoint。

每个 index root 同时只允许一个构建。持有 `.build.lock` 时，第二次构建立即失败。

使用本地 Ollama 全新构建时，通过其 OpenAI 兼容 `/v1` endpoint 使用已安装的 `qwen3-embedding` 模型。4B 模型产生 2560 维向量。使用该 endpoint 前创建 `num_ctx=32768` 的本地模型别名，因为 OpenAI 接口不能逐请求指定上下文大小。Compose base URL 使用 `http://host.docker.internal:11434/v1`，宿主机构建使用 `http://127.0.0.1:11434/v1`。本地服务的 API key 留空。将 UE Git tag 解析到的 commit 固定于 `CODALITH_UE_SOURCE_REVISION`，并将所选源码导出到单独的本地目录，避免改变其他检出。

重建向量时覆盖 indexing service 的 adopt 命令：

```bash
docker compose --profile ue run --rm index-ue index build --corpus ue-5.7.4 --semantic build
docker compose --profile ue up -d mcp-http-ue
```

索引器以跨文件共享的有界批次流式处理文件。语义 chunk 另有 8000 字节 UTF-8 输入上限；必要时拆分长行，并保留源码行号引用。Qwen3 查询添加通用检索指令，文档不加前缀。Semantic engine 1.1.0 因这些输入变化使早期 generation 失效。大规模构建前，对异常密集的数值 chunk 使用 Ollama 原生 `truncate=false` 进行预检，再确认 OpenAI endpoint 报告相同 token 数。

## 诊断

```bash
uv run codalith index status --target sample
uv run codalith doctor --target sample --deep
```

Deep doctor 对本地产物和 semantic store 的每个字节计算哈希，因此大型 UE store 可能较慢。指纹不一致、store 来源漂移、revision/config/source-policy 漂移、adapter 或 semantic engine 版本变化、缺失产物都会使 generation 无效，不会静默回退。未发布 generation 时状态为 `missing`，当前 generation 必须重建时为 `invalid`。

旧 generation 目录保留，用于诊断和修复指针时回退。开发阶段没有自动保留策略任务；仅在服务已停止且保全当前 generation 后，才清理旧 generation。

<a id="operations-dashboard"></a>

## 运维面板

容器镜像包含已构建面板，并设置 `CODALITH_DASHBOARD_DIR`。本地检出应在启动 HTTP transport 前先构建：

```bash
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard build
uv run codalith serve --transport http
```

打开 `http://127.0.0.1:8765/dashboard/`。面板显示 MCP 查询量与延迟、工具使用、进程 CPU 与峰值内存、语料 generation 大小和就绪状态、近期查询、结构化日志及当前事件。语料、时间范围、图表、日志级别、工具、搜索、暂停、刷新和导出控制均在浏览器本地生效。

后端 endpoint 为 `GET /api/dashboard/snapshot`。遥测仅在进程内保存且有上限（10,000 次调用、2,000 条日志）：记录工具、目标、查询或 URI 的前 180 个字符、耗时、结果数量、错误码和错误消息。HTTP 进程重启后清空。数据只通过执行 Host/Origin 检查的 HTTP 应用提供。

`CODALITH_DASHBOARD_DIR` 可显式选择资源目录；未设置时查找检出中的 `dashboard/dist`。资源不存在时，`/dashboard` 返回构建说明。

<a id="http-exposure"></a>

## HTTP 暴露

Compose 发布的端口默认绑定 `127.0.0.1`。仅在受信任本地 proxy 需要时添加精确的 Host/Origin 值。管理员 bearer 认证保护管理接口，不代表只读 MCP、catalog 和监控接口也具有认证；应将完整服务放在合适的可信部署边界内，不直接暴露给不受信任网络。

<a id="shared-database-workflow"></a>

## 共享数据库操作流程

当前源码检出支持托管 Git 版本，同时保留目录型建库方式。先按[配置](../configuration/#shared-git-databases-and-management)设置 `[management]`、允许源根、profile 和管理员 token，构建 dashboard 后启动 HTTP 服务。启用管理的空注册表在版本尚未就绪时也能提供管理页面；首次发布前 `/readyz` 可能返回 503。

1. 打开 `/dashboard/#databases`，输入管理员 token 连接。它只保存在页面内存，不进入浏览器存储。
2. 浏览允许的**服务器**源码根目录，注册已有 Git 目录。支持普通和 bare 仓库，不导入工作树未提交修改。
3. 创建数据库并选择已有构建 profile，再选择仓库、搜索 tag，跨页多选版本。
4. 可选择旧 generation 复用经校验的兼容向量。点击 **Preflight selected tags**，核对固定 commit 与查询 target。
5. 开始构建。**Build jobs** 显示持久化批次与逐 tag 状态、真实阶段计数、已发布版本数、错误和事件。每个 tag 成功后独立可查，后续失败不撤销成功版本。

构建计划固定 tag 对象 ID 和解析后的 commit。源 tag 随后移动，不会改变已提交任务或覆盖已发布快照；指向相同有效快照的多个 tag 复用数据。读取 Git object 不切换分支、不执行 filters，也不拉取缺失对象。所选文件必须存在于本地；被选为源码的 LFS pointer 会使该版本失败，不递归 submodule 或 symlink。源码策略和 profile 限制继续生效。

Worker 同时执行一个构建，按顺序处理 tags。关闭页面不会取消任务；**Cancel remaining work** 在安全边界请求取消，进行中的 embedding 调用可能先完成，短暂的发布提交也允许结束。继续或重试仅处理未成功项，保留成功版本并复用已提交的对象和向量。

任务状态包括 `queued`、`running`、`succeeded`、`partial`、`failed`、`cancel_requested`、`cancelled` 和 `interrupted`；phase 区分文件导入、结构、embedding、索引维护与发布。Worker 中断后会先对照已发布快照恢复，包括崩溃前刚刚发布的版本，剩余工作由管理员手动继续。历史列表每五秒刷新，活动详情／事件每两秒刷新，请求不重叠。百分比使用可测工作量；索引操作没有可靠总量时显示阶段，不伪造百分比。

空间面板将共享池实际物理占用与各版本逻辑源码大小分开显示。源码、目录／结构、语义数据和历史索引具有不同开销；共享池统计不能作为各数据库独占大小相加。这里不承诺 UE 5.0～5.8 的固定节省比例或全量构建吞吐。

### 本地管理命令

CLI 与面板共用目录、计划和 worker 锁。以下命令使用配置的 registry 和源码策略；需要其他文件时，将全局 `--registry`、`--policy` 放在 `management` 前面。

```bash
uv run codalith management config
uv run codalith management repositories --root-id 0 --path UnrealEngine
uv run codalith management tags REPO_ID --query 5.7 --limit 25
uv run codalith management databases --name "Unreal Engine" --repo-id REPO_ID --profile-id ue-shared
uv run codalith management plan --database-id DATABASE_ID --repo-id REPO_ID --tag 5.7.4-release --tag 5.8.0-release
uv run codalith management build --plan-id PLAN_ID --idempotency-key MY_UNIQUE_REQUEST
uv run codalith management jobs --job-id JOB_ID
uv run codalith management cancel JOB_ID
uv run codalith management resume JOB_ID
uv run codalith management retry JOB_ID
uv run codalith management space --database-id DATABASE_ID
uv run codalith management gc --dry-run
```

将大写 ID 替换为前序命令返回的值，tag 必须真实存在于所选仓库。同一个幂等键只用于重复提交同一份计划。`build --submit-only` 仅入队，由 HTTP 服务 worker 执行；否则 CLI 会尝试执行。`resume`、`retry` 将未成功工作重新入队，由运行中的 worker 领取。未运行 HTTP worker 时，再次用原计划和同一幂等键执行 `management build`，可在 CLI 执行已重新入队的任务。

### 导入旧 generation

保留旧目录与服务，在新的管理存储根目录旁路创建共享数据库。面板列出已配置目录 corpus 的当前 generation；CLI 接受其中的 generation ID：

```bash
uv run codalith management plan --database-id DATABASE_ID --repo-id REPO_ID --tag 5.7.4-release --legacy-generation GENERATION_ID
uv run codalith management build --plan-id PLAN_ID --idempotency-key LEGACY_IMPORT_REQUEST
```

导入器检查旧 manifest／产物、文件哈希、记录的 provider/model/dimension 和 embedding 输入格式。仅复用与所选 Git 快照源码及完整输入一致的向量，缺失或不兼容输入需要新 embedding。每版本统计分别报告导入向量、缺失输入、不兼容输入，以及源码和解析复用量；产物损坏会使构建失败。来源信息不完整不能证明模型权重相同。旧符号 ID 按新快照重新绑定；旧 generation、`current.json` 和外部 adopted store 不变。先核对 MCP 结果和源码引用，再将客户端切到新 target，不自动删除旧数据。

`management gc --dry-run` 只报告不可达候选与保留版本，不删除文件。首版没有开启破坏性共享池 GC；不要手动执行 Lance cleanup 或删除内部保留 tag，已发布快照依赖这些 table version。

<a id="project-version-binding"></a>

## 项目版本绑定

在持有 UE 项目的客户端电脑上执行检测；服务器不能读取客户端 `.uproject` 或引擎安装：

```bash
uv run codalith project detect --project "/work/MyGame" --server "http://127.0.0.1:8765/mcp"
uv run codalith project bind --project "/work/MyGame" --server "http://127.0.0.1:8765/mcp" --agent codex --dry-run
uv run codalith project bind --project "/work/MyGame" --server "http://127.0.0.1:8765/mcp" --agent codex
```

已有绑定优先；否则读取选定 `.uproject` 的 `EngineAssociation`，定位已注册或明确指定的引擎，再读取 `Engine/Build/Build.version`。目录包含多个项目时指定 `--project-file Game.uproject`；无法自动定位引擎时指定 `--engine-root /path/to/UnrealEngine`；需要精确选择时使用 `--target TARGET_ID`。缺版本、歧义或冲突返回非零结果，不默认最新版本。本地可取得引擎 Git 来源时，commit 也必须匹配。

`bind` 写入 `.codalith/project.json`，通过安装器更新选定客户端的项目级配置，提供原始字节备份和失败回滚。绑定保存服务器、target、源码 revision 和本地元数据引用，不保存管理员 token。重复 `--agent` 可选择多个客户端；`--dry-run` 只预览，不写文件。引擎升级后需要重新检测并明确绑定，第三方 HTTP 客户端不会自动检查本地项目变化。

生成的 endpoint 为 `/mcp/targets/TARGET_ID`。工具省略 target 时自动绑定，其他 target 或其他 corpus 的源码／符号 URI 会被拒绝；此地址也拒绝跨版本 `codalith_compare`。有意比较版本时，使用普通 `/mcp` 并显式传入 `from_corpus` 和 `to_corpus`。`GET /api/catalog` 和 `codalith_catalog` 提供当前注册版本目录。

## 迁移说明

以下变化调整了持久化格式和公开契约。升级后应重建或重新 adopt 每个 generation；此前旧 generation 会报告 `invalid`。`adopt` 不重新 embedding。

- Semantic engine 2.0.0 引入按完整输入寻址的共享向量与版本成员关系；显式 legacy 导入器接受可验证的 1.1.0 和 2.0.0 输入。旁路迁移期间，保留旧服务二进制查询旧存储：原地升级该二进制后，其 1.1.0 语义 generation 会失效，直到重建或重新 adopt。导入到单独的 management 根目录，核对结果后再切换客户端；旧存储与旧服务不会自动切换。
- Manifest schema 4 完成将 CodeRAG 纳入 `codalith.semantic`：来源字段改为 `semantic_engine_version`、`semantic_store`、`semantic_store_fingerprint`、`semantic_covered_files` 和 `semantic_extra_files`；产物为 `semantic-meta.json`，新建 store 位于 generation 的 `semantic/`。Registry 的 `coderag_store` 改为 `semantic_store`；Compose 变量为 `CODALITH_UE_SEMANTIC_STORE_DIR` 和 `CODALITH_UE_SEMANTIC_HOST_DIR`。需重命名 `.env` 中的 `CODALITH_UE_CODERAG_HOST_DIR`，或移动 store，因为默认宿主机路径从 `.local/coderag/` 改为 `.local/semantic/`。`codalith_search` 的后端由 `coderag` 改报 `semantic`，fastembed 模型缓存位于 `~/.cache/codalith/fastembed`。
- Manifest schema 3：`source_selection_hash` 取代 `chunk_policy_hash`，当前包含 deny globs 和字节上限；当时语义 generation 记录 `coderag_store`、`coderag_covered_files` 和 `coderag_extra_files`。
- Structure schema 4 增加 `name_match` 引用解析、`symbol_search` trigram 索引，以及逐符号比较摘要。`cpp-ue` adapter 版本 6 不再把 `#include` 保存为模块依赖。构建要求 SQLite 支持 FTS5 与 trigram tokenizer（3.34+），容器镜像和标准 Python 构建提供这些能力。
- 移除 `--allow-external-rebuild`。`--semantic build` 始终构建 generation 本地 store，`--semantic adopt` 是使用外部 store 的方式。
- MCP 错误码使用固定契约，见 [MCP API](../mcp-api/)。`codalith_read` 响应和 graph 节点带 `generation_id`；graph 边可报告 `name_match`；`codalith_compare` 带 `warnings` 并在 30 秒预算后停止；源码 URI 范围要求 `#Lx-Ly`。
- `text` 搜索对完整 query 做字面匹配，不再需要 semantic store。
