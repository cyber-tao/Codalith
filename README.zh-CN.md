# Codalith

[English README](README.md)

Codalith 是一个面向版本化源码语料的 Python MCP 网关。核心保持领域中性：默认代码、配置、测试和 Docker 服务都使用一个小型 sample 源码语料；真实业务语料通过 `configs/corpus_registry.json` 声明。

UE 5.7 只出现在 `eval/` 中，因为当前检索质量基线依赖已有的 UE 5.7 源码 CodeRAG embedding store。它不是默认 MCP 服务路径的一部分。

## 功能

- 面向 AI 编程工具的 MCP stdio 与 Streamable HTTP 网关。
- 可配置 source root、indexed root、CodeRAG store、卡片、source priors 和 seed cards 的版本化语料注册表。
- 统一 `codalith://<corpus_id>/...` 资源 URI。
- `SourceReader` 读取源码时优先 source root，indexed root 仅兜底。
- 源码读取策略、scope、速率限制和审计日志。
- 可选语义图 store 与 Knowledge Card 校验。
- 面向默认 sample 与显式 UE eval 的 CodeRAG acceptance / eval 报告。

## 环境要求

- Python 3.11 或更新版本。
- 本地 Python 流程使用 [uv](https://docs.astral.sh/uv/)。
- 容器化验证使用 Docker Compose。
- 运行 native CodeRAG acceptance 时需要初始化固定的 CodeRAG submodule。

## 快速开始

```bash
cp .env.example .env
uv sync --extra dev
uv run pytest
uv run codalith-mcp
```

本地启动 HTTP MCP server：

```bash
uv run codalith-mcp-http --host 127.0.0.1 --port 8765 --endpoint /mcp
```

HTTP endpoint：

```text
http://127.0.0.1:8765/mcp
```

## 配置

默认本地开发使用 `fixtures/sample_corpus`：

- `configs/corpus_registry.json`
- `configs/source_policy.json`
- `configs/source_priors.json`
- `configs/seed_cards.json`

机器相关配置放在 `.env`，不要为不同机器直接改 `docker-compose.yml`。

常用变量：

| 变量 | 作用 |
| --- | --- |
| `CODALITH_SAMPLE_SOURCE_ROOT` | 默认 sample corpus 的源码根。 |
| `CODALITH_SAMPLE_INDEXED_ROOT` | 搜索/索引使用的 indexed root。 |
| `CODALITH_SAMPLE_CODERAG_STORE_DIR` | sample corpus 的 CodeRAG store 路径。 |
| `CODALITH_SAMPLE_SOURCE_PRIORS` | 可选 deterministic source prior 配置。 |
| `CODALITH_SAMPLE_SEED_CARDS` | 可选 seed card 配置。 |
| `CODALITH_SCOPES` | 显式 scope 覆盖；留空则授予基础 scope 加 registry 里的 access scopes。 |
| `CODALITH_CODERAG_PROVIDER` | 本地命令默认使用的 CodeRAG provider。 |

## Docker 工作流

运行默认检查：

```bash
docker compose run --rm test
```

启动 HTTP MCP 服务：

```bash
docker compose up -d mcp-http
```

运行默认 sample corpus acceptance：

```bash
docker compose --profile acceptance run --rm corpus-acceptance
```

对 sample dataset 运行 native CodeRAG acceptance：

```bash
docker compose --profile coderag run --rm coderag-acceptance
```

显式运行 UE eval profile：

```bash
docker compose --profile eval-ue run --rm ue-eval
```

UE profile 使用 `eval/configs/ue_5_7_4_registry.json`、`eval/configs/ue_source_priors.json`、`eval/configs/ue_seed_cards.json` 和 `eval/datasets/ue_eval_suite.jsonl`。

## CLI

| 命令 | 作用 |
| --- | --- |
| `codalith-mcp` | stdio MCP server。 |
| `codalith-mcp-http` | Streamable HTTP MCP server。 |
| `codalith-index-corpus --corpus <id>` | 索引任意已配置 corpus。 |
| `codalith-extract-semantic --corpus <id>` | 运行已配置语义 profile；无 profile 时成功 no-op。 |
| `codalith-generate-cards --corpus <id>` | 生成并校验配置的 seed cards。 |
| `codalith-verify-cards --corpus <id>` | 校验配置的 seed cards。 |
| `codalith-eval --dataset <path>` | 进程内 eval。 |
| `codalith-mcp-eval --endpoint <url> --dataset <path>` | 通过 MCP HTTP endpoint 运行 eval。 |

## 验证

```bash
uv run pytest
uv run ruff check src tests jobs
uv run mypy src
docker compose config --quiet
docker compose --env-file .env.example config --quiet
```

UE 源码/checkpoint store 可用时的显式 eval：

```bash
uv run python -m codalith.eval.runner --registry eval/configs/ue_5_7_4_registry.json --dataset eval/datasets/ue_eval_suite.jsonl --output-dir reports/eval/ue_eval_suite --version 5.7.4 --max-source-spans 20
```
