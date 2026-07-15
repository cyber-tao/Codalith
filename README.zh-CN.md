# Codalith

[English](README.md)

Codalith 是为 Claude Code、Codex 等 AI 编程工具提供源码查询能力的本地优先 MCP 服务。它把不可变的 SQLite 结构索引与 CodeRAG 检索结合起来，通过官方 MCP 协议返回有范围限制、可校验哈希的源码证据。

核心框架不绑定领域。当前 Python、C# 与 C++/Unreal Engine 提供结构化适配器；其他文本语料可使用通用适配器，但不会伪造符号能力。

## 核心保证

- 每条源码结果都携带 corpus revision 和不可变 generation id。
- `codalith://` 引用只能读取该代结构索引中存在的文件。
- 源码读取受统一 deny policy 与行数/字节数上限约束，并报告索引后变更。
- 只有全部产物校验成功后，才通过一次原子指针替换发布新代。
- stdio 与 Streamable HTTP 暴露同一组七个严格、只读 MCP 工具。
- HTTP 默认仅绑定本机，校验 Host/Origin，并限制分块请求体总量。

Codalith 不是托管式多租户网关，因此不再包含鉴权、RBAC、审计数据库、限流壳、Knowledge Cards、source priors 或旧 PostgreSQL semantic store。

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Git submodule
- Docker Compose（可选）

## 快速开始

```bash
git submodule update --init --recursive
uv sync --frozen --extra dev
uv run codalith index build --corpus sample --semantic build
uv run codalith doctor --target sample --deep
uv run codalith serve --transport http
```

MCP 地址为 `http://127.0.0.1:8765/mcp`。以下命令只输出配置，不会修改用户目录：

```bash
uv run codalith client-config --client codex --transport http
uv run codalith client-config --client claude --transport stdio
```

Docker Compose 会先构建 sample 索引，再启动服务：

```bash
docker compose up --build mcp-http
```

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `codalith_search` | 结构、语义或精确文本检索 |
| `codalith_context` | 生成带已校验源码的有界上下文包 |
| `codalith_read` | 按 canonical source URI 精确读取 |
| `codalith_symbol` | 精确或模糊解析符号定义 |
| `codalith_graph` | 有界遍历入边/出边引用图 |
| `codalith_compare` | 比较两个语料的结构符号 |
| `codalith_status` | 无副作用读取 readiness 与 provenance |

完整输入输出与错误语义见 [MCP API](docs/mcp-api.md)。

## 目录结构

```text
configs/                 TOML corpus registry 与源码策略
benchmarks/datasets/     sample、UE 回归集与独立 holdout
external/CodeRAG/        固定提交的检索引擎 submodule
fixtures/sample_corpus/  确定性本地冒烟语料
src/codalith/corpus/     registry、policy、URI、generation、源码读取
src/codalith/languages/  结构适配器协议与实现
src/codalith/indexing/   SQLite 构建器与 CodeRAG 集成
src/codalith/query/      与传输无关的查询服务
src/codalith/mcp/        官方 SDK 绑定与传输层
src/codalith/benchmarks/ 真实 MCP benchmark runner
src/codalith/cli/        单一 `codalith` 命令树
tests/                   单元、集成、安全与协议测试
```

## 开发验证

```bash
uv run --frozen ruff check src tests
uv run --frozen mypy src
uv run --frozen pytest -q
docker compose config --quiet
docker compose --profile ue config --quiet
docker compose --profile test config --quiet
```

对真实 MCP 地址运行独立数据集：

```bash
uv run codalith benchmark \
  --dataset benchmarks/datasets/sample-smoke.jsonl \
  --endpoint-url http://127.0.0.1:8765/mcp \
  --label local-sample
```

## 进一步文档

- [架构与不变量](docs/architecture.md)
- [配置](docs/configuration.md)
- [MCP API](docs/mcp-api.md)
- [运维与 UE 配置](docs/operations.md)
- [评测](docs/evaluation.md)

项目当前处于开发期。索引与配置 schema 变更后直接重建 generation，不保留兼容壳。
