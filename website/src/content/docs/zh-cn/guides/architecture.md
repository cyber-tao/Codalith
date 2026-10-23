---
title: 架构与不变量
description: 了解不可变 generation、结构和语义检索、语言 adapter、引用解析与本地安全边界。
---

Codalith 有一个独立于传输的查询核心，以及两个检索平面：

1. 结构平面是由语言 adapter 构建的不可变 SQLite 快照。
2. 语义平面是绑定到该快照的混合检索 store，引擎位于 `codalith.semantic`。`--semantic build` 从快照文件的物化副本构建 generation 自身的 store；`--semantic adopt` 对照快照验证外部 store 并固定其路径。

Query service 融合两种平面，是 MCP transport 使用的唯一 API。HTTP 应用还提供运维面板；MCP 绑定自身不导入 dashboard 代码。

## Corpus 与 workspace

Corpus 标识一个源码根目录、revision、索引根目录、adapter、embedding 配置、扩展名 allow-list 和排除路径。Workspace 是有序的 corpus ID 列表。ID 使用小写、URI 安全的值，在 corpus 和 workspace 之间全局唯一。

版本比较表示为两个 corpus，没有隐式 overlay 或可变项目 corpus。

## Generation 布局

```text
<index_root>/
  current.json
  .build.lock
  generations/
    <timestamp>-<digest>/
      manifest.json
      structure.sqlite
      semantic-meta.json
      semantic/
```

`.build.lock` 在构建期间持有；有语义检索时才存在 `semantic-meta.json`；`semantic/` 仅用于 build，采用的 store 保留在外部。

构建在 index root 持有操作系统级锁，写入隐藏的 staging 目录，验证 SQLite 完整性，准备语义平面，重新计算每个索引源码文件的哈希，对 generation 执行 fsync，将 staging 重命名为最终 generation ID，再通过已 fsync 的临时文件原子替换 `current.json`。构建失败不会改变当前 generation 及其 store。旧 generation 不会被修改或静默删除。

Manifest（schema 4）绑定源码 revision 和指纹、adapter ID 和版本、源码选择哈希、结构 schema、semantic engine 版本、embedding provider、模型和维度、semantic store 位置、指纹和覆盖率、计数以及本地产物哈希。源码选择哈希包含 corpus 扩展名、excludes、源码策略 deny globs 和字节上限。配置、策略、adapter 或 semantic engine 版本漂移会使当前 generation 无效，必须重建。

## 语义平面

- **Build**：将快照文件物化为临时视图，按文本索引到尚不存在的 store。除了没有可索引文本的空文件或二进制文件外，每个快照文件均须以相同 SHA-256 存入。
- **Adopt**：不写外部 store。引擎以只读方式打开已有 store，记录的模型或维度与配置 provider 不一致时拒绝；adopt 还拒绝任何与快照哈希不同的共有文件，记录快照覆盖文件数与 store 额外文件数，在 manifest 固定 store 绝对路径。
- **Query**：先对照 manifest 验证 store 的来源信息，再打开 store；使用 manifest 中的 embedding provider 配置，而不是当前配置。舍弃快照外命中，并依据采用的 store 中额外文件比例扩大候选数量。

每个语义 generation 记录 `codalith.semantic.ENGINE_VERSION`。修改分块、embedding 输入或 store 布局需要提升版本，从而使旧 generation 失效。

## 结构 schema

- `files`：路径、语言、SHA-256、字节数、行数和模块。
- `symbols`：ID 由路径、限定名称、类型、签名和起始行生成，在 generation 内唯一，但符号移动时会变化。另含限定/普通名称、类型、签名、范围、父符号、元数据，以及 `codalith_compare` 比较字段的摘要。摘要与 comparison key 建索引，比较按该索引流式进行，只有摘要不同的组才加载完整记录。
- `symbol_search`：名称与限定名称的 FTS5 trigram 索引。Rowid 为符号排名（定义优先，再按 profile 声明路径，再按较短限定名称）；模糊查找按完整名称、前缀、子串的排名顺序读取，到达 limit 即停止，无需排序全部匹配。短于三个字符的查询也按相同顺序扫描。
- `references`：可解析时的 source/target ID、原始 target、解析状态和证据行。
- `module_dependencies`：adapter 提取的模块边，包括 Python imports、C# `using` namespace 和有类型的 `.Build.cs` 依赖。C++ `#include` 不是模块边，其首个路径段是头文件目录，并非模块。

引用解析保持保守。限定 target 恰好匹配一个符号时才为 `resolved`；target 最后一个名称段恰好匹配整个 corpus 的唯一符号时为 `name_match`，因为不会分析 receiver type 和 imports。多个候选保持 `ambiguous`，无候选保持 `unresolved`。Graph 响应原样携带此状态。

## 语言 adapter

Adapter 实现 `supports(path)` 和 `extract(path, text)`，不访问文件系统或数据库。无依赖的 `codalith.languages.catalog` 是 adapter ID、版本、retrieval profile 和扩展名到语言映射的来源。Adapter 在创建时才加载 parser。

- Python 使用标准库 AST。
- C# 使用 tree-sitter 公开 API 提取类型、可调用项、成员、调用、namespace 和 using 边。
- C++/UE 使用 tree-sitter 公开 API、保留字节位置的反射/导出宏遮罩、UE 元数据提取和有类型的 `.Build.cs` 依赖；嵌入的 C# 文件使用 C# adapter。
- Generic 仅索引文件来源信息。

改变提取语义或 retrieval profile 需要提升 catalog 版本。Profile 也决定固化到 `symbol_search` 中的符号排名。

## 检索与上下文

每次请求只解析一次每个目标 corpus，并保持租用对应 generation 的资源直到结束。因此一次调用的结果不会混合 generation，并发发布也不会关闭正在使用的资源；被替代的资源在最后一个请求结束后关闭。

`auto` 搜索融合候选标识符的精确符号查找、按名称和路径词的文件查找，以及 generation store 中的语义命中，通过加权 reciprocal-rank fusion 排名。引擎命名和目录惯例，例如 Unreal 类型前缀、`*.Build.cs` 规则文件、头文件或 `Public/` 优先级，仅经 adapter 的 `RetrievalProfile` 传入查询层。检索代码不编码评测问题或答案。`text` 对索引文件中的完整 query 做不区分大小写的字面匹配，无需语义平面。

所有输出通过源码策略。被拒路径不出现在 search、context、symbol 和 graph 结果中，并报告于 warnings。

`codalith_context` 去除重叠范围，执行片段和字符预算，通过策略层读取当前源码，行数上限或预算截短的片段标为 `truncated`，并报告过期哈希或解码替换。只有结构与语义证据相互支持时置信度才为 high；降级或过期结果为 low。

## 本地安全边界

Codalith 假设操作方受信任，不以恶意租户为使用场景。安全设计集中于源码约束和浏览器/网络暴露：

- 规范的相对源码路径和 generation 成员资格。
- 在读取和每个查询输出中执行 deny globs、字节与行数硬上限。
- 默认回环地址；所有 HTTP 路由，包括面板和健康 endpoint，均执行防 DNS rebinding 的 Host 及 Origin allow-list。
- 对完整流式请求设置固定的缓冲上限。
- 不记录请求/响应体；面板遥测只保留有界的进程内查询预览。
- 没有携带 secret 的 auth/database 接口面。
