# Codalith Engineering Guide

本文件是仓库唯一的 agent 规则源；`AGENTS.md` 只负责指向这里。

## 工作方式

- 默认处于 Development 阶段：优先 correctness、clean design、maintainability，不保留旧接口兼容层。
- 开始前执行 `git status --short`，保护用户已有改动；只精确暂存当前任务文件。
- 修改后依次运行相关测试、`ruff`、`mypy`，涉及部署时再跑 Compose 和真实 MCP。
- 验证通过后创建英文 Conventional Commit 的本地 checkpoint；不得自主 push、tag、release 或修改分支。
- `.env`、`.local/`、`data/`、`reports/`、编辑器配置和用户 MCP 配置不得提交；只有用户明确要求本地语料验收时才读取必要的路径配置，绝不输出密钥值。

## 当前架构

```text
static TOML registry + source policy
          |                         |
          v                         v
directory generation        managed Git tags -> shared source/fact/vector pool
immutable SQLite/store      durable management catalog + immutable snapshot releases
          |                         |
          +-----------+-------------+
                      v
                 QueryService
                 /          \
            MCP stdio    MCP HTTP + dashboard + authenticated management routes
```

模块边界：

- `codalith.corpus`：配置、canonical URI、源策略、generation manifest、源码读取与只读 Git object backend；不导入 tree-sitter，也不导入语义引擎的 store 或 embedding 实现。
- `codalith.languages`：无 I/O 的结构抽取协议；`catalog` 是 adapter id、version、`RetrievalProfile` 与扩展名映射的唯一来源；UE 特性只存在于 `cpp_ue` 适配器和 UE profile。
- `codalith.semantic`：分块、embedding、LanceDB 与 dense+BM25 混合检索；只认识显式 `Config`，不导入 Codalith 其他包。
- `codalith.indexing`：`builder` 编排并原子发布目录 generation；`shared` 管理自管 Git 源码对象、共享结构事实、版本成员关系及逐 tag 原子发布；`structure` 提供快照查询视图，`semantic` 绑定语义后端，另含字面文本检索。
- `codalith.management`：静态管理配置、SQLite WAL 控制目录、仓库/数据库/构建计划、持久任务与受管 worker、管理员 HTTP 路由；写操作不得加入 MCP 工具。
- `codalith.query`：检索融合、context/read/symbol/graph/compare/status；每个请求对每个语料只解析一次 generation。
- `codalith.mcp`：官方 MCP SDK 绑定；不放业务逻辑，`server`/`stdio` 不导入 `codalith.dashboard`。
- `codalith.dashboard`：进程内查询遥测与面板 HTTP 路由，只由 HTTP 应用组装；后台任务的持久状态属于 management catalog，不能依赖遥测内存。
- `codalith.project`：本地 UE 工程版本检测、精确版本匹配与项目级 MCP 绑定；不让远程服务读取客户端文件，不保存管理员 token。
- `codalith.benchmarks`：必须通过真实 Streamable HTTP MCP client 评测。
- `codalith.cli`：唯一入口为 `codalith`。

## 不变量

- `configs/registry.toml` 与 `configs/source-policy.toml` 是静态配置入口，路径相对声明文件解析。TOML 声明目录语料、workspace、管理存储/允许源根与构建 profile；management catalog 独占动态仓库、数据库、计划和任务记录；已提交 shared snapshot 记录是动态查询版本的来源，不反写 TOML，不允许静态/动态 target ID 冲突。源策略的 deny globs 与字节上限属于快照有效性。
- `current.json` 只能指向完整且已发布的不可变 generation；失败构建不得改变它或任何已发布代的 store。
- 语义 store 只能来自同一代快照：`build` 在 generation 内按 `structure.sqlite` 文件集合精确构建到尚不存在的目录；`adopt` 必须校验交集文件哈希、记录覆盖率并固定 store 路径；已有 store 只读打开，打开前必须校验其记录的 embedding model/dimension，不一致即拒绝。
- 共享池可追加，已发布 Git snapshot 不可变：逐 tag 固定 source commit、成员集合、配置指纹、Lance table version 与内部保留 tag；dense/FTS 必须在 top-K 前按版本过滤。追加不得修改旧版源内容、位置或检索统计，不以整个可变池目录的哈希验证旧快照。
- 相同源码/解析事实/完整 embedding 输入跨版本复用；路径与行号独立绑定，路径敏感 adapter 的缓存键包含路径上下文，跨文件引用按快照解析。共享池构建串行、查询可并行；失败或取消不替换已发布快照。
- 用户选择的 Git 源目录只读，不 checkout/fetch/push，不运行 filters，不隐式拉取缺失对象；应用自管 `sources.git` 可写且仅存选定源码对象。策略在虚拟相对路径上继续执行，read/text 查询已发布自管对象而非原工作树。
- 旧 generation、`current.json` 与 adopted store 保留；显式 legacy 导入只有来源/输入/模型配置验证通过才复用向量。共享池不得自动 compact/prune 或删除保留 tag；GC 当前仅 dry-run，不能把候选统计当成删除许可。
- 管理接口使用环境提供的至少 16 字符管理员 token，不能进入 URL、项目配置、日志或 MCP 输出。目录浏览限允许根；任务与事件持久化，重启后未完成项手动继续，已发布项先核对后恢复。
- `codalith_read` 只能读取结构索引中的相对路径，并比较当前 SHA-256 与 indexed SHA-256；deny 策略同时作用于所有工具输出。
- `codalith_status` 不加载 embedding model、不扫描源码、不触发建库。
- MCP schema `extra=forbid`；所有公开工具只读、幂等、closed-world；错误码由 `codalith.errors` 显式声明。
- HTTP 默认 loopback publish，Host/Origin 白名单（覆盖所有路由）和完整流式 body 上限不可绕过。
- 绑定 MCP endpoint 自动填充该 target 并拒绝跨 target 参数/URI/compare；普通 endpoint 的跨版 compare 必须显式指定语料。绑定是查询版本约束，不是替代管理认证的权限系统。
- 新 adapter、adapter 语义或其 `RetrievalProfile` 变更必须提升 `catalog` 中的 `version`，旧 generation 自动失效（profile 决定 `symbol_search` 中固化的符号排序）。
- 语义引擎的分块、embedding 输入或 store 布局变更必须提升 `codalith.semantic.ENGINE_VERSION`，旧的语义 generation 自动失效。
- 检索代码不得编码评测问句、关键词或期望答案路径；语言/引擎约定只能经 adapter 的 `RetrievalProfile` 进入查询层。

## 常用命令

```bash
uv sync --frozen --extra dev
uv run codalith index build --corpus sample --semantic build
uv run codalith doctor --target sample --deep
uv run --frozen ruff check src tests
uv run --frozen mypy src
uv run --frozen pytest -q
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard typecheck
bun run --cwd dashboard test
docker compose config --quiet
docker compose --profile ue config --quiet
docker compose --profile test config --quiet
```

真实服务验收顺序：sample/UE generation 就绪 → `/readyz` → 官方 MCP `initialize`/`tools/list` → benchmark → citation `codalith_read` 复核。详细流程见 `docs/operations.md` 与 `docs/evaluation.md`。
