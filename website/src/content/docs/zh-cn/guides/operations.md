---
title: 运维
description: 运行本地或 Compose 服务，准备 UE 索引，诊断 generation，并使用运维面板。
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

## UE 5.7.4

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

Compose 发布的端口默认绑定 `127.0.0.1`。仅在受信任本地 proxy 需要时添加精确的 Host/Origin 值。Codalith 没有网络认证层，不应直接暴露到不受信任网络。

## 迁移说明

以下变化调整了持久化格式和公开契约。升级后应重建或重新 adopt 每个 generation；此前旧 generation 会报告 `invalid`。`adopt` 不重新 embedding。

- Manifest schema 4 完成将 CodeRAG 纳入 `codalith.semantic`：来源字段改为 `semantic_engine_version`、`semantic_store`、`semantic_store_fingerprint`、`semantic_covered_files` 和 `semantic_extra_files`；产物为 `semantic-meta.json`，新建 store 位于 generation 的 `semantic/`。Registry 的 `coderag_store` 改为 `semantic_store`；Compose 变量为 `CODALITH_UE_SEMANTIC_STORE_DIR` 和 `CODALITH_UE_SEMANTIC_HOST_DIR`。需重命名 `.env` 中的 `CODALITH_UE_CODERAG_HOST_DIR`，或移动 store，因为默认宿主机路径从 `.local/coderag/` 改为 `.local/semantic/`。`codalith_search` 的后端由 `coderag` 改报 `semantic`，fastembed 模型缓存位于 `~/.cache/codalith/fastembed`。
- Manifest schema 3：`source_selection_hash` 取代 `chunk_policy_hash`，当前包含 deny globs 和字节上限；当时语义 generation 记录 `coderag_store`、`coderag_covered_files` 和 `coderag_extra_files`。
- Structure schema 4 增加 `name_match` 引用解析、`symbol_search` trigram 索引，以及逐符号比较摘要。`cpp-ue` adapter 版本 6 不再把 `#include` 保存为模块依赖。构建要求 SQLite 支持 FTS5 与 trigram tokenizer（3.34+），容器镜像和标准 Python 构建提供这些能力。
- 移除 `--allow-external-rebuild`。`--semantic build` 始终构建 generation 本地 store，`--semantic adopt` 是使用外部 store 的方式。
- MCP 错误码使用固定契约，见 [MCP API](../mcp-api/)。`codalith_read` 响应和 graph 节点带 `generation_id`；graph 边可报告 `name_match`；`codalith_compare` 带 `warnings` 并在 30 秒预算后停止；源码 URI 范围要求 `#Lx-Ly`。
- `text` 搜索对完整 query 做字面匹配，不再需要 semantic store。
