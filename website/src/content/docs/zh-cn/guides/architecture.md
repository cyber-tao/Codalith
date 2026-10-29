---
title: 架构与不变量
description: 了解共享不可变快照、目录 generation、结构与语义检索及源码约束。
---

Codalith 有一个独立于传输的查询核心，以及两个检索平面：

1. 结构平面是语言 adapter 事实的逻辑快照，通过目录 generation 的 SQLite 或共享事实的版本视图查询。
2. 语义平面是绑定到快照的混合检索 store，引擎位于 `codalith.semantic`。目录 build/adopt 继续使用 generation 绑定的 store；托管 Git 快照共享唯一代码块和向量，同时固定允许成员与 Lance table version。

Query service 融合两种平面，是 MCP transport 使用的唯一 API。HTTP 应用还提供运维面板及独立认证的管理接口。MCP 工具保持只读，建库、任务控制与 Git 操作不进入 MCP 工具。

## Corpus 与 workspace

Corpus 标识一个源码根目录、revision、索引根目录、adapter、embedding 配置、扩展名 allow-list 和排除路径。Workspace 是有序的 corpus ID 列表。ID 使用小写、URI 安全的值，在 corpus 和 workspace 之间全局唯一。

版本比较表示为两个 corpus，没有隐式 overlay 或可变项目 corpus。

静态 TOML 声明目录 corpus、workspace、源码策略及可选管理根目录／profile。Management SQLite catalog 保存动态仓库、数据库、计划和任务。已发布托管 target 只来自已提交的 shared snapshot 记录；创建数据库或任务入队不使未建成版本可查，静态与动态 target ID 不得冲突。

<a id="generation-layout"></a>

## 目录 generation 布局

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

## 共享 Git 快照布局

```text
<management.storage_root>/
  management.sqlite           # 持久管理记录与任务事件
  worker/                     # 单 worker 锁
  shared/
    catalog.sqlite            # 共享事实、成员、位置和已发布快照
    sources.git/              # 应用自管 bare Git 源码对象库
    semantic/<namespace>/
      chunks.lance/           # 唯一正文/向量记录与保留的版本
```

源仓库仅作为只读输入。保存的构建计划固定 tag 对象 ID 与解析后的 commit；直接读取本地 tree/blob，不 checkout、联网 fetch 或执行工作树 filters。源码策略及 profile 按虚拟仓库相对路径选择文件。对象导入应用自管池后，原仓库后续变动或被移走，也不改变已发布源码读取和字面文本检索。

相同文件字节共享源码对象。源对象格式及 OID 到已验证受管对象的映射避免重复读取未变 blob；普通 tag 构建不再重打包整个共享池。Adapter 解析及分块结果按内容／配置缓存；UE 文件移动可能改变模块，因此解析键包含路径上下文。共享事实暂存省略最终查询索引和 VACUUM，由发布的共享目录持有索引及查询规划统计。结构事实与版本成员／绑定分离，跨文件引用解析按快照保存。文件字节不变并不保证解析后的图边不变。

语义命名空间包含 provider/model/dimension 和输入配置。完整 embedding 输入相同便共享向量，不受版本、路径或行号变化影响；occurrence 恢复所选快照的位置。每个已发布语义快照固定一个不可变布尔成员列及 BITMAP 索引；dense 和 FTS 在 top-K 前按成员过滤，再只恢复该快照内的位置。

每个 tag 在源码对象、事实、成员及必要索引持久化后独立发布。快照记录固定内容／配置指纹、语义命名空间、成员列、Lance table version 和内部保留 tag。池追加不会使旧快照指纹失效，查询打开固定版本而非最新池状态；导入新版本时，旧快照的 FTS 统计也保持稳定。

重试先校验并复用已保留的 release，再发布目录记录。共享结构连接使用自动提交及固定快照的成员视图，缓存查询资源不会长期占用 WAL 读事务。共享语义资源通过 generation lease 复用 provider 和固定版本的表句柄；服务关闭时，仍在使用的资源会等最后一个请求结束再释放。

共享池构建锁保证单写，读请求继续查询已发布快照。支持时通过增量 delta segment 维护搜索索引；只有明确不支持增量的能力错误才重建对应索引，资源、权限、取消或损坏错误不触发更重的重建。不自动执行全表 compact、Lance cleanup 或删除保留 tag。即使正文／向量 fragment 共享，历史搜索索引和成员元数据仍有额外占用，空间统计单独报告。

控制目录持久保存任务 attempt、逐 tag 状态和有序事件。Worker 重启先与已提交快照核对，再将未完成任务留待手动继续；刚发布后崩溃不能使该版本被误判缺失。重复请求复用计划和幂等键，重试复用已提交对象。GC 当前仅报告候选，不删除数据。

## 语义平面

以下 build/adopt 描述目录 generation 路径。托管 Git 构建使用上面的共享快照模型；显式 legacy 导入校验目录 generation，将兼容向量复制到新池，不改变旧产物或指针。

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

`codalith_context` 去除重叠范围，执行片段和字符预算，通过策略层读取源码，行数上限或预算截短的片段标为 `truncated`，并报告过期哈希或解码替换。目录 generation 比较工作目录源码与已索引哈希，托管快照读取保留的 Git 源对象。只有结构与语义证据相互支持时置信度才为 high；降级或过期结果为 low。

`codalith_catalog` 不加载 embedding 模型即可列出版本。本地 project detector 将 `.uproject`／引擎元数据与版本目录匹配，显式 binder 保存精确 target/revision 并安装绑定 HTTP endpoint。绑定地址补齐省略的 target、拒绝越界 target/URI；有意跨版本比较使用普通 `/mcp`。项目路径和管理员凭证不发送到版本目录。

## 本地安全边界

Codalith 假设操作方受信任，不以恶意租户为使用场景。安全设计集中于源码约束和浏览器/网络暴露：

- 规范的相对源码路径和 generation 成员资格。
- 在读取和每个查询输出中执行 deny globs、字节与行数硬上限。
- 默认回环地址；所有 HTTP 路由，包括面板和健康 endpoint，均执行防 DNS rebinding 的 Host 及 Origin allow-list。
- 对完整流式请求设置固定的缓冲上限。
- 不记录请求/响应体；面板遥测只保留有界的进程内查询预览。
- 管理路由要求管理员 bearer 认证，凭证仅来自配置的进程环境及请求头，不进入查询工具、项目绑定或遥测。
- 管理操作限定服务器源码根及固定 Git 命令，不开放远程 Git transport 或任意 shell 执行。
- 只读 MCP/catalog/监控路由与管理员权限分离，部署时仍需合适的可信边界。
