# Codalith — AI 上下文索引（根级）

> 仓库只维护这一份根级 `CLAUDE.md`；模块细节以源码和本文件索引为准。

## 当前状态

Codalith 是面向**版本化源码语料**的 Python MCP 网关。默认服务使用 sample corpus；完整 UE 5.7.4 是 `configs/corpora/ue-5.7.4/` 下的可选产品语料，真实检索验收使用 `eval/datasets/ue_eval_suite.jsonl` 与本地 native CodeRAG store。

2026-07-10 完成开发期全库收口：

- MCP stdio/Streamable HTTP 迁移到官方 Python MCP SDK v1。
- corpus registry 强校验 kind、revision、唯一 alias/default 和 overlay base 绑定。
- native/local retrieval backend 解耦；native store 校验 Codalith sidecar manifest；fallback 与局部 reindex 语义修复。
- Context Pack 使用 canonical source slice/hash，显式标记 stale index，并限制 query fan-out。
- Knowledge Card 以 `card_root` Markdown 为唯一真相源，区分 `evidence_verified` / `semantic_verified`。
- semantic store 使用线程本地连接、显式 schema version、跨方言 upsert；图边聚合 evidence 并保持节点/边截断完整。
- MCP 工具统一使用 `corpus` selector，资源支持 source/project/generated，examples 不返回绕过审计的源码正文。
- UE 默认 pytest 只验证 dataset contract；真实 gate 强制 native、zero fallback、manifest、citation/version 与适用指标。
- CLI 迁入 `src/codalith/cli/`，配置按 sample 与产品 corpus 分层。

## 架构

1. **配置/错误层**（`codalith.config`, `codalith.errors`）：JSON/env placeholder、统一异常。
2. **语料层**（`codalith.corpus`）：registry、target resolution、URI、SourcePolicy、canonical `SourceSlice`、store manifest。
3. **检索层**（`codalith.coderag`）：`CodeRAGAdapter` 门面、native/local backend、bounded local index。
4. **语义层**（`codalith.semantic`）：versioned SQLite/PostgreSQL store，通用 modules/module_deps/symbols/compile_guards/graph_edges。
5. **卡片层**（`codalith.cards`）：seed schema、hash、Markdown round-trip、filesystem repository、verifier。
6. **编译层**（`codalith.compiler`）：intent/entity detection、source priors、检索/rerank、card evidence、graph expansion、Context Pack。
7. **网关层**（`codalith.gateway`）：官方 MCP SDK binding、stdio、Streamable HTTP、auth、policy、rate limit、audit。
8. **评估层**（`codalith.eval`）：共享 metrics/gates、in-process 与官方 MCP client runner。
9. **CLI 层**（`codalith.cli`）：索引、卡片、semantic status、CodeRAG/UE acceptance、backup。

`codalith_context` 数据流：

```text
query + corpus/project
-> CorpusRegistry.resolve
-> intent / identifiers / modules
-> source priors + RetrievalBackend.search
-> rerank + canonical SourceSlice
-> FileCardRepository + verified evidence
-> semantic graph
-> typed ContextPack
```

## 路径索引

| 模块 | 路径 |
| --- | --- |
| 核心包 | `src/codalith/` |
| MCP 网关 | `src/codalith/gateway/` |
| corpus | `src/codalith/corpus/` |
| retrieval | `src/codalith/coderag/` |
| compiler | `src/codalith/compiler/` |
| cards | `src/codalith/cards/` |
| semantic | `src/codalith/semantic/` |
| eval | `src/codalith/eval/` |
| CLI | `src/codalith/cli/` |
| sample 配置 | `configs/sample/` |
| UE 配置 | `configs/corpora/ue-5.7.4/` |
| 共享 source policy | `configs/source_policy.json` |
| eval datasets | `eval/datasets/` |
| sample fixture | `fixtures/sample_corpus/` |
| 外部 submodule | `external/CodeRAG/` |

## 本地运行

```bash
cp .env.example .env
uv sync --extra dev
uv run codalith-mcp
uv run codalith-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp
```

HTTP endpoint：`http://127.0.0.1:8765/mcp`

UE 使用 `.env.ue.example` 作为追加模板，不把本机路径或凭证提交到仓库。

## 入口

| 命令 | Python 入口 |
| --- | --- |
| `codalith-mcp` | `codalith.gateway.mcp_server:main` |
| `codalith-mcp-http` | `codalith.gateway.http_server:main` |
| `codalith-eval` | `codalith.eval.runner:main` |
| `codalith-mcp-eval` | `codalith.eval.mcp_runner:main` |
| `codalith-index-corpus` | `codalith.cli.index_corpus:main` |
| `codalith-semantic-status` | `codalith.cli.semantic_status:main` |
| `codalith-generate-cards` | `codalith.cli.generate_cards:main` |
| `codalith-verify-cards` | `codalith.cli.verify_cards:main` |
| `codalith-coderag-acceptance` | `codalith.cli.coderag_acceptance:main` |
| `codalith-backup-coderag-store` | `codalith.cli.backup_coderag_store:main` |
| `codalith-ue-eval` | `codalith.cli.ue_eval:main` |

## 验证

默认门禁：

```bash
uv run pytest
uv run ruff check src tests scripts/normalize_eval_dataset.py
uv run mypy src
docker compose config --quiet
docker compose --env-file .env.example config --quiet
uv run python scripts/normalize_eval_dataset.py --check
```

真实 UE gate：

```bash
uv run codalith-ue-eval \
  --source-root /path/to/UnrealEngine_5.7 \
  --indexed-root /path/to/UnrealEngine_5.7 \
  --store-dir .local/coderag-openai-store/ue-5.7.4-openai-qwen3-embedding-8b-3072c-3584b-full
```

要求：`count == 80`、全部 applicable row 为 `pass`、native backend、`native_fallbacks == 0`、store manifest validated。

## Agent 注意事项

- 默认按开发阶段处理，优先 correctness、clean design、maintainability，不保留旧接口兼容。
- 每次任务先执行 `git status --short` 并保护用户已有改动。
- `.cursor/`、`.codex/`、`.vscode/mcp.json`、`.env`、`.local/`、`data/`、`reports/` 不读取或提交（用户明确要求读取本地语料资产时除外）。
- 不把 UE 领域逻辑放入 `src/codalith` 核心；UE 内容只通过 corpus 配置/dataset 进入。
- 不修改 `external/CodeRAG` submodule，除非任务明确要求。
- Semantic schema 不做隐式旧库迁移；开发期变更后重建 store。
- 不自主 push。
