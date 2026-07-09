# Codalith — AI 上下文索引（根级）

> 本仓库当前只维护这一份根级 `CLAUDE.md`；模块细节以源码与本文件索引为准。

## 变更记录 (Changelog)

| 时间 | 动作 | 说明 |
| --- | --- | --- |
| 2026-07-09 | 全库收口 | 修复 `wrong_version_rate`（按非 overlay span 锚定）；`codalith_examples` scope 由 registry `scope_prefixes` 动态生成；统一 URI line-fragment 解析；`resolve(project)` 在 overlay 关闭时仍解析 `base_corpus`；删除 `list_source_files`/`token_set`/`mechanisms` stub/`semantic_profile`；`knowledge_cards`→`codalith_knowledge_cards`，卡片目录 `KNOWLEDGE`→`cards`；`extract-semantic`→`semantic-status`；抽出 `aggregate_graph_neighborhood`；Context Pack TypedDict；UE 测试迁至 `tests/eval/`。 |
| 2026-07-08 | 结构收口 | 语义层删除 reflection/target/plugin/project 残留模型，收敛为 modules/module_deps/symbols/compile_guards/cards/graph_edges；registry 配置键 `engines` 更名 `corpora`（`get_base` / `CorpusResolution.base` / `base_corpus`）；coderag adapter 拆分为 `types/local_index/native/adapter`；eval 双 runner 共享 `evaluate_dataset` 核心，报告键统一为 `file_recall@k` + `metric_k`；jobs 通过 `jobs/common.py` 统一 corpus 解析；删除 `fixtures/project_overlay` 孤儿 fixture 与历史 reports 产物。 |
| 2026-07-08 | 去 UE 定向化 | Codalith 默认定位收口为通用源码语料 MCP 网关：默认 registry / compose / tests 使用 `sample-codebase`，核心源码不再包含 UE extractor、旧 UE schema migration 或 UE 默认路径。UE 5.7 仅保留为 `eval/` 专用 registry、source priors、seed cards 和 dataset，因为当前 CodeRAG embedding 质量基线来自 UE 5.7 源码。 |

## 项目愿景

Codalith 是一个面向**版本化源码语料**的 Python MCP（Model Context Protocol）网关。内核只认 corpus 抽象，对 MCP 客户端的能力声明由 `configs/corpus_registry.json` 驱动。领域知识必须通过 corpus 显式配置进入，例如 `source_priors_path` 和 `seed_cards_path`；默认服务路径不假设任何特定引擎或框架。

UE 5.7 不是产品默认目标，只是当前评估数据源。相关配置限定在 `eval/configs/`，数据集限定在 `eval/datasets/ue_eval_suite.jsonl`。

## 架构总览

1. **配置与错误层**（`codalith.config`, `codalith.errors`）：JSON 配置加载、环境变量占位符展开、统一异常体系。
2. **语料层**（`codalith.corpus`）：`CorpusRegistry`（配置键 `corpora`/`projects`/`generated`，`get_base` 解析默认语料）、`URIResolver`、`uris` URI 原语（含共享 `parse_line_fragment` / `parse_source_uri`）、`SourcePolicy`、`SourceReader`（读失败抛 `SourceReadError`）。
3. **检索适配层**（`codalith.coderag`）：`adapter.CodeRAGAdapter` 门面 + `types.RetrievalHit` + `local_index`（确定性本地倒排索引 fallback）+ `native`（native CodeRAG 实例构建与运行时桥接）。
4. **语义层**（`codalith.semantic`）：SQLite/Postgres 语义存储，通用模型为 modules / module_deps / symbols / compile_guards / knowledge_cards（表名 `codalith_knowledge_cards`）/ graph_edges；core 不含任何领域 extractor。
5. **编译层**（`codalith.compiler`）：意图识别、实体检测（CamelCase 与 snake_case）、corpus-local source priors、检索规划、重排、证据选择，输出 Context Pack；卡片 `verification_status` 取自卡片 front-matter。
6. **卡片层**（`codalith.cards`）：seed card schema、生成、哈希、Markdown 渲染、验证、`is_card_path` 判定（渲染目录 `cards/`）。
7. **网关层**（`codalith.gateway`）：MCP stdio + Streamable HTTP、工具/资源/审计/鉴权；协议版本常量 `mcp_server.PROTOCOL_VERSION`。
8. **任务层**（`jobs/`）：CLI 入口，公共 corpus 解析在 `jobs/common.py`。
9. **评估层**（`codalith.eval`）：指标、共享 `evaluate_dataset` 核心与 in-process / MCP 两个 runner；默认 sample dataset，UE eval 需显式传 eval registry/dataset。

一次 `codalith_context` 调用的数据流：

```text
query
-> ContextCompiler.compile
-> registry.resolve(version, project)  # CorpusResolution(base, project, overlays)
-> detect_intent / detect_identifiers / detect_modules
-> locate_source_priors(corpus.source_priors_path)
-> _build_queries -> CodeRAGAdapter.search_code
-> rerank -> source spans / cards / graph edges
-> ContextPack
```

## 模块索引

| 模块 | 路径 | 一句话职责 |
| --- | --- | --- |
| codalith | `src/codalith/` | 包入口、配置、错误、文本原语 |
| gateway | `src/codalith/gateway/` | MCP stdio / HTTP 网关、工具、资源、审计、鉴权 |
| corpus | `src/codalith/corpus/` | 语料注册表、URI 解析、源码读取策略、SourceReader |
| coderag | `src/codalith/coderag/` | 检索类型、native CodeRAG 桥接、本地确定性索引、adapter 门面 |
| compiler | `src/codalith/compiler/` | Context Pack 编译 |
| semantic | `src/codalith/semantic/` | 语义图 store 与通用语义类型（modules/symbols/guards/cards/edges） |
| cards | `src/codalith/cards/` | Knowledge Card 生成、哈希、渲染、验证 |
| eval | `src/codalith/eval/` | eval metrics、in-process runner、MCP runner |
| jobs | `jobs/` | CLI 任务脚本 |
| tests | `tests/` | pytest 测试套件（UE 基准在 `tests/eval/`） |
| scripts | `scripts/` | MCP 客户端安装与 UE MCP eval 辅助脚本 |
| configs | `configs/` | 默认 sample corpus 配置 |
| eval/configs | `eval/configs/` | UE eval 专用配置 |
| eval/datasets | `eval/datasets/` | sample eval 与 UE Eval Suite |
| fixtures/sample_corpus | `fixtures/sample_corpus/` | 默认本地开发和测试语料 |
| external/CodeRAG | `external/CodeRAG/` | Git submodule，外部依赖 |

## 运行与开发

### 环境要求

- Python 3.11+
- uv
- Docker Compose
- Git submodule：`external/CodeRAG`（仅 native CodeRAG acceptance 需要）

### 本地运行

```bash
cp .env.example .env
uv sync --extra dev
uv run codalith-mcp
uv run codalith-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp
```

HTTP 端点：`http://127.0.0.1:8765/mcp`

### Docker 工作流

```bash
docker compose run --rm test
docker compose up -d mcp-http
docker compose --profile acceptance run --rm corpus-acceptance
docker compose --profile coderag run --rm coderag-acceptance
docker compose --profile coderag run --rm coderag-openai-acceptance
docker compose --profile eval-ue run --rm ue-eval
```

### 入口脚本

| 脚本 | 入口 |
| --- | --- |
| `codalith-mcp` | `codalith.gateway.mcp_server:main` |
| `codalith-mcp-http` | `codalith.gateway.http_server:main` |
| `codalith-eval` | `codalith.eval.runner:main` |
| `codalith-mcp-eval` | `codalith.eval.mcp_runner:main` |
| `codalith-index-corpus` | `jobs.index_corpus:main` |
| `codalith-semantic-status` | `jobs.semantic_status:main` |
| `codalith-generate-cards` | `jobs.generate_cards:main` |
| `codalith-verify-cards` | `jobs.verify_cards:main` |
| `codalith-coderag-acceptance` | `jobs.coderag_acceptance:main` |
| `codalith-backup-coderag-store` | `jobs.backup_coderag_store:main` |

## 测试策略

默认门禁：

```bash
uv run pytest
uv run ruff check src tests jobs
uv run mypy src
docker compose config --quiet
docker compose --env-file .env.example config --quiet
```

UE eval 显式命令：

```bash
uv run python -m codalith.eval.runner --registry eval/configs/ue_5_7_4_registry.json --dataset eval/datasets/ue_eval_suite.jsonl --output-dir reports/eval/ue_eval_suite --version 5.7.4 --max-source-spans 20
```

通过标准：UE Eval Suite `count == 80`，`file_recall@k == 1.000`（`metric_k == 5`），`candidate_file_recall == 1.000`，`module_accuracy == 1.000`，HTTP MCP eval 的 `failure_class` 全部为 `pass`（可用 `codalith-mcp-eval --require-pass` 自动门禁）。

## Agent 注意事项

- 默认按开发阶段处理，优先正确设计和清晰实现，不保留旧 UE 默认兼容。
- 开始任务先执行 `git status --short` 并保护用户已有改动。
- `.cursor/`、`.codex/`、`.vscode/mcp.json` 是本地配置，不读取、不提交。
- 不要把 UE 相关代码放回 `src/codalith` 默认核心路径；如需评估 UE 检索质量，只使用 `eval/` 下的显式配置和数据集。
- CodeRAG 子模块不要做无关修改。
