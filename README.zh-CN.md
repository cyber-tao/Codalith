# Codalith

[English README](README.md)

Codalith 是一个面向版本化源码语料的 Python MCP 网关，为 Claude Code、Codex 等 AI 编程工具提供有源码依据的 Context Pack、受限源码读取、符号/语义图查询、用例检索和语料比较。

核心保持领域中性。默认服务使用 `fixtures/sample_corpus`；UE 5.7.4 是 `configs/corpora/ue-5.7.4/` 下的可选产品语料，必须显式挂载 native CodeRAG store 并通过独立验收套件。

## 架构

- 官方 Python MCP SDK v1 提供 stdio 与 Streamable HTTP。
- 强校验的 `source` / `project` / `generated` corpus registry 与 revision provenance。
- 带 manifest 强校验的 native CodeRAG backend，以及有容量上限的确定性本地 fallback。
- Canonical source slice、读取策略、按身份限流和审计记录。
- 以文件系统为唯一真相源、区分 evidence/semantic 验证状态的 Knowledge Card。
- 可选、带 schema version 的 SQLite/PostgreSQL 语义图。
- in-process 与 MCP runner 共享 eval 指标和门禁。

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- 容器工作流需要 Docker Compose
- 仅 native 检索/验收需要 `external/CodeRAG` submodule

## 快速开始

```bash
cp .env.example .env
uv sync --extra dev
uv run pytest
uv run codalith-mcp
```

HTTP：

```bash
uv run codalith-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp
```

端点：`http://127.0.0.1:8765/mcp`

安装 MCP 客户端配置：

```bash
sh scripts/install-mcp-client.sh
# 或
powershell -File scripts/install-mcp-client.ps1
```

## 配置

默认 sample 资产：

- `configs/sample/registry.json`
- `configs/sample/source_priors.json`
- `configs/sample/seed_cards.json`
- `configs/source_policy.json`

UE 产品资产：

- `configs/corpora/ue-5.7.4/registry.json`
- `configs/corpora/ue-5.7.4/source_priors.json`
- `configs/corpora/ue-5.7.4/seed_cards.json`
- `configs/corpora/ue-5.7.4/store_manifest.json`
- `eval/datasets/ue_eval_suite.jsonl`

中性开发只复制 `.env.example`。使用 UE 时再把 `.env.ue.example` 的值追加到本地 `.env`，并在本机替换相对 host path 和凭证。不要提交 `.env`、store、report 或源码挂载。

产品 corpus 必须声明非空 `source_revision`。配置 manifest 的 native store 如果 model、dimension、schema、corpus 或 revision 不匹配，会被直接拒绝。

## CLI

| 命令 | 作用 |
| --- | --- |
| `codalith-mcp` | MCP stdio server |
| `codalith-mcp-http` | MCP Streamable HTTP server |
| `codalith-index-corpus --corpus <id>` | 索引或 smoke-check corpus |
| `codalith-semantic-status --corpus <id>` | 记录/报告 semantic store |
| `codalith-generate-cards --corpus <id>` | 生成 evidence-verified 卡片 |
| `codalith-verify-cards --corpus <id>` | 验证配置卡片 |
| `codalith-coderag-acceptance --corpus <id>` | native CodeRAG 验收 |
| `codalith-backup-coderag-store` | 备份 CodeRAG store |
| `codalith-eval --corpus <id>` | 进程内 eval |
| `codalith-mcp-eval --corpus <id>` | 经 MCP HTTP eval |
| `codalith-ue-eval` | 跨平台真实 UE MCP 验收 |

## Docker

```bash
docker compose run --rm test
docker compose up -d mcp-http
docker compose --profile acceptance run --rm corpus-acceptance
docker compose --profile coderag run --rm coderag-acceptance
```

在 `.env` 中配置 UE host path 与查询 embedding provider 后：

```bash
docker compose --profile ue up -d mcp-http-ue
docker compose --profile eval-ue run --rm ue-eval
```

两个 UE 服务都强制 native strict，并以只读方式挂载指定 store 目录。

## Eval

默认 pytest 只验证 80 条 UE dataset contract，不再构造假 UE 检索满分。真实验收必须显式运行：

```bash
uv run codalith-ue-eval \
  --source-root /path/to/UnrealEngine_5.7 \
  --indexed-root /path/to/UnrealEngine_5.7 \
  --store-dir .local/coderag-openai-store/ue-5.7.4-openai-qwen3-embedding-8b-3072c-3584b-full
```

门禁要求 80 条、全部适用的文件/module/symbol 指标通过、citation/version 错误为零、backend 为 native、fallback 为零且 store manifest 已验证。数据集期望可用以下命令校验：

```bash
uv run python scripts/normalize_eval_dataset.py --check
```

## 验证

```bash
uv run pytest
uv run ruff check src tests scripts/normalize_eval_dataset.py
uv run mypy src
docker compose config --quiet
docker compose --env-file .env.example config --quiet
```

Semantic store 带 schema version。本开发版本会明确拒绝无版本的旧 store；应重建，而不是隐式迁移。
