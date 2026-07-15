# Codalith Engineering Guide

本文件是仓库唯一的 agent 规则源；`AGENTS.md` 只负责指向这里。

## 工作方式

- 默认处于 Development 阶段：优先 correctness、clean design、maintainability，不保留旧接口兼容层。
- 开始前执行 `git status --short`，保护用户已有改动；只精确暂存当前任务文件。
- 修改后依次运行相关测试、`ruff`、`mypy`，涉及部署时再跑 Compose 和真实 MCP。
- 验证通过后创建英文 Conventional Commit 的本地 checkpoint；不得自主 push、tag、release 或修改分支。
- `.env`、`.local/`、`data/`、`reports/`、编辑器配置和用户 MCP 配置不得提交；只有用户明确要求本地语料验收时才读取必要的路径配置，绝不输出密钥值。
- 不修改 `external/CodeRAG` 子模块内部源码；依赖行为通过 Codalith 的公开适配层约束。

## 当前架构

```text
TOML registry + source policy
            |
            v
language adapter -> immutable SQLite structure.sqlite
            |                  + CodeRAG generation/store
            +----------------------------+
                                         v
                                  QueryService
                                  /          \
                             MCP stdio   MCP HTTP
```

模块边界：

- `codalith.corpus`：配置、canonical URI、源策略、generation manifest、源码读取。
- `codalith.languages`：无 I/O 的结构抽取协议；UE 特性只存在于 `cpp_ue` 适配器。
- `codalith.indexing`：构建并原子发布 SQLite/CodeRAG generation。
- `codalith.query`：检索融合、context/read/symbol/graph/compare/status。
- `codalith.mcp`：官方 MCP SDK 绑定；不放业务逻辑。
- `codalith.benchmarks`：必须通过真实 Streamable HTTP MCP client 评测。
- `codalith.cli`：唯一入口为 `codalith`。

## 不变量

- `configs/registry.toml` 与 `configs/source-policy.toml` 是唯一配置入口，路径相对声明文件解析。
- `current.json` 只能指向完整且已发布的不可变 generation；失败构建不得改变它。
- CodeRAG 建库文件集合必须来自同一代 `structure.sqlite`，不得自行扩大遍历范围。
- `codalith_read` 只能读取结构索引中的相对路径，并比较当前 SHA-256 与 indexed SHA-256。
- `codalith_status` 不加载 embedding model、不扫描源码、不触发建库。
- MCP schema `extra=forbid`；所有公开工具只读、幂等、closed-world。
- HTTP 默认 loopback publish，Host/Origin 白名单和完整流式 body 上限不可绕过。
- 新 adapter 或 adapter 语义变更必须提升 `version`，旧 generation 自动失效。

## 常用命令

```bash
uv sync --frozen --extra dev
uv run codalith index build --corpus sample --semantic build
uv run codalith doctor --target sample --deep
uv run --frozen ruff check src tests
uv run --frozen mypy src
uv run --frozen pytest -q
docker compose config --quiet
docker compose --profile ue config --quiet
```

真实服务验收顺序：sample/UE generation 就绪 → `/readyz` → 官方 MCP `initialize`/`tools/list` → benchmark → citation `codalith_read` 复核。详细流程见 `docs/operations.md` 与 `docs/evaluation.md`。
