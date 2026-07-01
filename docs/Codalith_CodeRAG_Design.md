# Codalith powered by CodeRAG 项目设计文档

> 版本：0.1 Draft  
> 日期：2026-06-30  
> 目标读者：技术负责人、AI Coding Agent、本地实现团队、引擎开发工程师  
> 设计目标：基于 CodeRAG 构建一个面向 UE5 项目开发的 MCP 源码真值服务，使 Claude Code、Codex、Cursor 等 AI 工具在回答 UE 相关问题或修改 UE 项目代码前，优先查询版本化、证据化、可追溯的 UE 源码上下文。

---

## 目录

1. [一句话定位](#1-一句话定位)
2. [背景与问题](#2-背景与问题)
3. [目标与非目标](#3-目标与非目标)
4. [基线判断：为什么以 CodeRAG 为底](#4-基线判断为什么以-coderag-为底)
5. [总体架构](#5-总体架构)
6. [核心设计原则](#6-核心设计原则)
7. [系统组件](#7-系统组件)
8. [仓库结构建议](#8-仓库结构建议)
9. [Corpus 与多版本管理](#9-corpus-与多版本管理)
10. [URI 体系](#10-uri-体系)
11. [CodeRAG 适配层](#11-coderag-适配层)
12. [UE Semantic Layer](#12-ue-semantic-layer)
13. [Knowledge Cards 与 CLAUDE.md 定位](#13-knowledge-cards-与-claudemd-定位)
14. [Context Compiler](#14-context-compiler)
15. [Context Pack Schema](#15-context-pack-schema)
16. [MCP Gateway 设计](#16-mcp-gateway-设计)
17. [MCP Tools 规格](#17-mcp-tools-规格)
18. [MCP Resources 规格](#18-mcp-resources-规格)
19. [Project Overlay](#19-project-overlay)
20. [安全、授权与审计](#20-安全授权与审计)
21. [索引管线](#21-索引管线)
22. [数据库模型](#22-数据库模型)
23. [配置文件](#23-配置文件)
24. [客户端接入策略](#24-客户端接入策略)
25. [评估体系](#25-评估体系)
26. [实施路线](#26-实施路线)
27. [第一版验收标准](#27-第一版验收标准)
28. [风险与缓解](#28-风险与缓解)
29. [给本地 AI 工具的实现任务拆分](#29-给本地-ai-工具的实现任务拆分)
30. [开放问题](#30-开放问题)
31. [参考来源](#31-参考来源)

---

## 1. 一句话定位

**Codalith powered by CodeRAG** 是一个面向 AI Coding Agent 的 UE 源码上下文服务。

它不是普通代码搜索服务，也不是单纯的 Markdown 知识库，而是：

```text
CodeRAG Retrieval Core
+ UE Semantic Index
+ UE Knowledge Cards
+ Context Compiler
+ MCP Gateway
+ Source Security / Audit
+ Project Overlay
```

一句话说明职责：

> CodeRAG 负责“快速找到相关代码”；Codalith 负责“判断这些代码在 UE 的版本、模块、反射、构建、平台、项目语境中到底意味着什么”；MCP Gateway 负责“把这个证据包安全地交给 Claude Code、Codex、Cursor”。

---

## 2. 背景与问题

### 2.1 当前问题

UE5 项目开发中，AI 工具经常出现以下问题：

```text
1. 凭模型记忆回答 UE 内部实现。
2. 搞错 UE 版本。
3. 把 UE4 / UE5.0 / UE5.3 / UE5.7 的行为混在一起。
4. 把 Runtime 模块、Editor 模块、Plugin 模块混淆。
5. 忽略 Build.cs、uplugin、Target.cs 的模块依赖语义。
6. 忽略 UHT、UCLASS、UPROPERTY、UFUNCTION、generated.h、gen.cpp。
7. 把 WITH_EDITOR / PLATFORM_* / UE_BUILD_SHIPPING 等条件编译路径当成通用行为。
8. 找到源码片段但不能解释其在 UE 架构中的位置。
9. 无法把项目源码与 UE 源码联合分析。
10. 无源码引用、无行号、无证据链，难以审查。
```

### 2.2 核心诉求

当用户在 Claude Code、Codex、Cursor 等 AI 工具中提出 UE5 相关问题时，AI 应该优先从 MCP 服务端获得：

```text
- 当前项目使用的 UE 版本。
- 相关模块。
- 相关符号。
- 相关源码路径和行号。
- 相关 UHT / reflection 信息。
- 相关 Build.cs / uplugin / Target.cs 关系。
- 相关项目源码 overlay。
- 可追溯证据。
- 可审计源码读取记录。
```

### 2.3 为什么不能只用 CLAUDE.md

CLAUDE.md 对 AI 友好，但不能作为事实源：

```text
- 它是自然语言，可能过期或遗漏。
- 它难以表达完整符号、模块、反射、构建、条件编译关系。
- 它可能把 AI 第一次分析源码时的误解固化成长期知识。
- 它不能替代源码级 evidence。
```

因此本项目中：

```text
CLAUDE.md = Knowledge Cards 的 Markdown 渲染物
不是主索引
不是事实源
不是最终证据
```

---

## 3. 目标与非目标

### 3.1 目标

```text
G1. 复用 CodeRAG 作为通用代码检索底座。
G2. 为 UE 源码建立版本化 corpus。
G3. 为 UE 建立 Semantic Layer：模块、符号、UHT、reflection、Build.cs、compile guards。
G4. 提供 MCP Streamable HTTP Gateway。
G5. 对外暴露 UE-aware tools，而不是裸露 CodeRAG generic tools。
G6. 返回 Context Pack，而不是散乱搜索结果。
G7. 通过 source URI、行号、source hash 提供证据链。
G8. 支持项目源码 overlay。
G9. 强制源码读取授权、行数限制和审计。
G10. 建立 UE eval 数据集，持续衡量准确率和召回率。
```

### 3.2 非目标

第一版不追求：

```text
N1. 完整 C++ call graph。
N2. 完整 clangd / libclang 级别语义索引。
N3. 完整 UBT 执行级模型。
N4. 完整 generated code 全量索引。
N5. 对外提供 UE 源码下载能力。
N6. 替代 IDE、Rider、Visual Studio、UnrealVS。
N7. 替代 Epic 官方文档。
N8. 让 MCP 服务端强制所有 AI 工具一定调用它；MCP 工具调用由客户端和模型决定。
```

---

## 4. 基线判断：为什么以 CodeRAG 为底

### 4.1 CodeRAG 已有能力

CodeRAG 已经实现了大量我们不应重复造的底层能力：

```text
- local-first code search。
- hybrid vector + keyword retrieval。
- CLI。
- Python library。
- HTTP / REST API。
- Web UI。
- MCP server。
- search_code。
- search_files。
- get_file。
- index_status。
- reindex。
- path:line citation。
- 增量索引。
- watcher。
- eval harness。
```

因此本项目不从零做：

```text
- vector index。
- BM25。
- reciprocal rank fusion。
- local embedding。
- basic MCP search tools。
- file range read。
- index watcher。
```

### 4.2 CodeRAG 不足以直接满足 UE 目标

CodeRAG 是通用代码 RAG，不是 UE 语义系统。特别是：

```text
- C/C++ 当前主要走 line-window fallback，不是稳定的类/函数/方法级 symbol-aware chunking。
- 不理解 Build.cs / Target.cs / uplugin / uproject。
- 不理解 UHT、UCLASS、UFUNCTION、UPROPERTY、generated.h。
- 不理解 UE module public/private dependency。
- 不理解 WITH_EDITOR、PLATFORM_*、UE_BUILD_SHIPPING 等 compile guards。
- 不理解项目源码与 Engine 源码的 overlay 关系。
- 不提供 UE-specific Context Pack。
- 不提供企业级 UE 源码授权和审计策略。
```

### 4.3 结论

```text
CodeRAG = Retrieval Core
Codalith = UE-aware Context Layer
```

不直接裸用 CodeRAG MCP，不直接暴露 search_code / get_file 给 AI。

对外暴露：

```text
codalith_context
codalith_lookup_symbol
codalith_read_source
codalith_graph
codalith_examples
codalith_compare_versions
codalith_index_status
```

内部再调用 CodeRAG：

```text
search_code
search_files
get_file
index_status
reindex
```

---

## 5. 总体架构

```text
Claude Code / Codex / Cursor / Other MCP Hosts
        │
        │ MCP Streamable HTTP
        ▼
┌────────────────────────────────────────────┐
│ Codalith MCP Gateway                      │
│ - Auth / RBAC                               │
│ - Audit                                     │
│ - MCP tools/resources/prompts               │
│ - Source access policy                      │
└──────────────────┬─────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────┐
│ Codalith Compiler                         │
│ - Intent detection                          │
│ - UE entity detection                       │
│ - Retrieval planning                        │
│ - CodeRAG orchestration                     │
│ - UE graph expansion                        │
│ - Reranking                                 │
│ - Evidence selection                        │
│ - Context Pack generation                   │
└──────────────┬─────────────────────────────┘
               │
       ┌───────┴──────────┐
       ▼                  ▼
┌───────────────┐   ┌────────────────────────┐
│ CodeRAG Core   │   │ UE Semantic Layer       │
│ - search_code  │   │ - Module graph          │
│ - search_files │   │ - Plugin graph          │
│ - get_file     │   │ - C++ symbol index      │
│ - index_status │   │ - UHT/reflection graph  │
│ - reindex      │   │ - Compile guards        │
│ - vector/BM25  │   │ - Generated code map    │
│ - eval harness │   │ - Knowledge Cards       │
└───────┬───────┘   └──────────┬─────────────┘
        │                      │
        └──────────┬───────────┘
                   ▼
┌────────────────────────────────────────────┐
│ Corpus Store                               │
│ - UE source snapshots                       │
│ - Project source overlays                   │
│ - UE_KNOWLEDGE generated cards              │
│ - CodeRAG indexes per corpus                │
│ - Semantic DB / graph edges                 │
└────────────────────────────────────────────┘
```

---

## 6. 核心设计原则

### 6.1 Evidence-first

所有重要结论都必须能回到：

```text
version
source_commit
module
symbol
file path
line range
source URI
extractor
confidence
```

### 6.2 CodeRAG 不做 UE 真值层

CodeRAG 负责召回候选，不能把 C++ line-window chunk 当成 UE symbol truth。

### 6.3 UE 语义单独建库

以下事实必须进入 UE Semantic DB：

```text
Build.cs dependencies
uplugin modules
C++ class/function/macro symbols
UCLASS/UFUNCTION/UPROPERTY metadata
generated.h relation
compile guards
module ownership
project overlay relation
```

### 6.4 Context Pack，不是搜索结果

AI 工具最终需要的是：

```text
最小可用上下文包
+ 源码证据
+ 相关模块
+ 相关符号
+ caveats
+ recommended next calls
```

而不是 20 条无组织搜索结果。

### 6.5 安全默认拒绝

UE 源码服务必须按内部源码系统处理：

```text
默认认证
默认只读
默认限行
默认审计
默认禁止 bulk export
默认不暴露 CodeRAG 原生 HTTP API
```

### 6.6 Project Overlay 是一等能力

真正提升团队效率的是：

```text
UE Engine Source
+ Project Source
+ Project Plugins
+ Config
+ Build.cs
+ Generated code
+ Logs / crash / build errors
```

---

## 7. 系统组件

### 7.1 MCP Gateway

职责：

```text
- 提供 MCP Streamable HTTP endpoint。
- 注册 UE-aware tools。
- 注册 UE resources / templates。
- 处理 auth、RBAC、source policy、audit。
- 不直接暴露 CodeRAG 原生 MCP server。
```

### 7.2 Context Compiler

职责：

```text
- 接收自然语言 query。
- 判断 intent。
- 抽取 UE entity。
- 编排 CodeRAG search_code / search_files。
- 查询 Semantic DB。
- 查询 Knowledge Cards。
- 图谱扩展。
- 重排序。
- 选择源码证据。
- 返回 Context Pack。
```

### 7.3 CodeRAG Adapter

职责：

```text
- 封装 CodeRAG Python API 或内部服务。
- 统一 search_code / search_files / get_file / status / reindex。
- 将 CodeRAG hit 映射成统一 RetrievalHit。
- 不承担 UE 语义判断。
```

### 7.4 Corpus Manager

职责：

```text
- 管理 UE version corpus。
- 管理 Project corpus。
- 管理 indexed root。
- 管理 CodeRAG store。
- 管理 source snapshot。
- 管理 publish / ready / stale 状态。
```

### 7.5 Semantic Indexer

职责：

```text
- 解析 Build.cs。
- 解析 Target.cs。
- 解析 uplugin / uproject。
- 抽取 C++ symbol-lite。
- 抽取 UHT/reflection metadata。
- 抽取 compile guards。
- 建立 graph edges。
```

### 7.6 Knowledge Card System

职责：

```text
- 生成 ModuleCard / SymbolCard / MechanismCard / RecipeCard / VersionDiffCard。
- 校验每个 claim 的 evidence。
- 将 verified card 渲染为 Markdown。
- 写入 UE_KNOWLEDGE，让 CodeRAG 索引。
```

### 7.7 Security / Audit

职责：

```text
- 用户身份。
- corpus scope。
- source scope。
- project scope。
- platform/NDA scope。
- source read rate limit。
- source read audit log。
- bulk export detection。
```

---

## 8. 仓库结构建议

```text
codalith/
  README.md
  pyproject.toml
  docker-compose.yml

  configs/
    corpus_registry.yaml
    source_policy.yaml
    mcp_server.yaml

  src/
    codalith/
      __init__.py

      gateway/
        mcp_server.py
        tools.py
        resources.py
        prompts.py
        auth.py
        audit.py
        errors.py

      compiler/
        context_compiler.py
        intent_detector.py
        entity_detector.py
        retrieval_planner.py
        reranker.py
        evidence_selector.py
        context_pack.py

      coderag/
        adapter.py
        result_mapper.py
        query_builder.py

      corpus/
        registry.py
        snapshot.py
        uri_resolver.py
        source_policy.py
        publish.py

      semantic/
        db.py
        schema.sql
        graph.py
        extractors/
          build_cs.py
          target_cs.py
          uplugin.py
          uproject.py
          cpp_symbols.py
          uht_reflection.py
          compile_guards.py
          generated_code.py

      cards/
        schema.py
        generator.py
        verifier.py
        renderer.py
        builtins/
          modules/
          mechanisms/
          recipes/

      eval/
        runner.py
        metrics.py
        datasets/
          ue50.jsonl
          ue_core.jsonl
          ue_reflection.jsonl
          ue_build.jsonl
          ue_networking.jsonl
          ue_rendering.jsonl
          ue_project_overlay.jsonl

  jobs/
    index_engine.py
    index_project.py
    generate_cards.py
    verify_cards.py
    publish_corpus.py
    run_eval.py

  tests/
    test_uri_resolver.py
    test_build_cs_extractor.py
    test_uht_reflection_extractor.py
    test_context_compiler.py
    test_source_policy.py
```

---

## 9. Corpus 与多版本管理

### 9.1 Corpus 类型

```text
engine corpus:
  UE 源码版本，例如 ue-5.7.4、ue-5.8.0。

project corpus:
  项目源码，例如 ProjectA。

generated corpus:
  可选，用于存放 UHT generated code、build outputs、logs、crashes。

knowledge corpus:
  UE_KNOWLEDGE，通常作为 engine/project indexed root 的子目录进入 CodeRAG。
```

### 9.2 Corpus Registry 示例

```yaml
engines:
  ue-5.7.4:
    kind: engine
    ue_version: "5.7.4"
    source_commit: "UNKNOWN"
    source_root: "/srv/ue/5.7.4"
    indexed_root: "/srv/codalith/corpora/ue-5.7.4"
    coderag_store: "/var/lib/codalith/coderag/ue-5.7.4"
    semantic_schema: "ue_5_7_4"
    card_root: "/srv/codalith/cards/ue-5.7.4"
    default: true
    access_scopes:
      - "ue:5.7"
      - "source:read"

  ue-5.8.0:
    kind: engine
    ue_version: "5.8.0"
    source_commit: "UNKNOWN"
    source_root: "/srv/ue/5.8.0"
    indexed_root: "/srv/codalith/corpora/ue-5.8.0"
    coderag_store: "/var/lib/codalith/coderag/ue-5.8.0"
    semantic_schema: "ue_5_8_0"
    card_root: "/srv/codalith/cards/ue-5.8.0"
    access_scopes:
      - "ue:5.8"
      - "source:read"

projects:
  ProjectA:
    kind: project
    engine_corpus: "ue-5.7.4"
    source_root: "/srv/projects/ProjectA"
    indexed_root: "/srv/codalith/corpora/project-a"
    coderag_store: "/var/lib/codalith/coderag/project-a"
    semantic_schema: "project_a"
    card_root: "/srv/codalith/cards/project-a"
    access_scopes:
      - "project:ProjectA"
      - "source:read"
```

### 9.3 Indexed Root 结构

不要直接把裸 UE 源码目录交给 CodeRAG。为每个 corpus 创建索引用目录：

```text
/srv/codalith/corpora/ue-5.7.4/
  Engine/                         -> symlink /srv/ue/5.7.4/Engine
  Templates/                      -> optional symlink
  Samples/                        -> optional symlink

  UE_KNOWLEDGE/
    Modules/
      Core.md
      CoreUObject.md
      Engine.md
      Renderer.md
      NetCore.md
    Mechanisms/
      UObject_GC.md
      UHT_Reflection.md
      UPROPERTY_Replication.md
      Actor_Replication.md
      RPC_Dispatch.md
    Symbols/
      UObject.md
      UClass.md
      AActor.md
      UWorld.md
      TArray.md
      FName.md
    Build/
      Module_System.md
      Public_vs_Private_Dependency.md
      Target_Rules.md
```

---

## 10. URI 体系

### 10.1 目标

所有对外返回的源码、模块、符号、card 都用 URI，不暴露服务器真实路径。

### 10.2 URI 示例

```text
ue://5.7.4/source/Engine/Source/Runtime/Core/Public/Containers/Array.h
ue://5.7.4/source/Engine/Source/Runtime/Core/Public/Containers/Array.h#L120-L260

ue://5.7.4/module/Core
ue://5.7.4/module/CoreUObject
ue://5.7.4/plugin/GameplayAbilities

ue://5.7.4/symbol/TArray
ue://5.7.4/symbol/UObject
ue://5.7.4/symbol/AActor.BeginPlay

ue://5.7.4/reflection/uclass/AActor
ue://5.7.4/reflection/ufunction/AActor.BeginPlay
ue://5.7.4/reflection/uproperty/AActor.bReplicates

ue://5.7.4/card/module/CoreUObject
ue://5.7.4/card/mechanism/actor-replication
ue://5.7.4/card/mechanism/uprop-replicated-using

ue-project://ProjectA/source/Source/ProjectA/Inventory/InventoryComponent.cpp
ue-project://ProjectA/source/Source/ProjectA/Inventory/InventoryComponent.cpp#L40-L120
ue-project://ProjectA/symbol/UInventoryComponent
```

### 10.3 URI Resolver 输出

```json
{
  "corpus_id": "ue-5.7.4",
  "relative_path": "Engine/Source/Runtime/Core/Public/Containers/Array.h",
  "start_line": 120,
  "end_line": 260,
  "source_kind": "engine"
}
```

---

## 11. CodeRAG 适配层

### 11.1 适配目标

CodeRAG Adapter 只做一件事：把 CodeRAG 能力包装成内部稳定接口。

```text
search_code(corpus_id, query, top_k, filters)
search_files(corpus_id, pattern, target, file_glob, limit)
get_file(corpus_id, path, start_line, end_line)
status(corpus_id)
reindex(corpus_id, path, full)
```

### 11.2 RetrievalHit 标准化

所有来自 CodeRAG、Semantic DB、Cards、Graph 的候选都映射成统一类型：

```json
{
  "source": "coderag",
  "corpus_id": "ue-5.7.4",
  "uri": "ue://5.7.4/source/Engine/Source/...#L100-L150",
  "path": "Engine/Source/...",
  "start_line": 100,
  "end_line": 150,
  "title": "Actor.h:100-150",
  "snippet": "...",
  "score": 0.87,
  "kind": "window",
  "language": "cpp",
  "symbol": null,
  "module": "Engine",
  "reason": "CodeRAG hybrid retrieval hit.",
  "metadata": {
    "coderag_similarity": 0.83
  }
}
```

### 11.3 重要约束

```text
- CodeRAG C++ hit 的 symbol 字段不能作为权威符号信息。
- C++ 权威符号来自 UE Semantic Layer。
- CodeRAG get_file 必须经过 codalith_read_source 的权限、行数、审计包装。
- CodeRAG 原生 HTTP/MCP 不直接对最终用户开放。
```

---

## 12. UE Semantic Layer

### 12.1 为什么需要 Semantic Layer

UE 不是普通 C++ 项目。以下信息必须结构化索引：

```text
.Build.cs
.Target.cs
.uplugin
.uproject
PublicDependencyModuleNames
PrivateDependencyModuleNames
DynamicallyLoadedModuleNames
UCLASS
USTRUCT
UENUM
UFUNCTION
UPROPERTY
GENERATED_BODY
.generated.h
*.gen.cpp
WITH_EDITOR
UE_BUILD_SHIPPING
PLATFORM_*
```

### 12.2 Extractor v0 范围

第一版只做 4 个 extractor：

```text
1. Build.cs / module extractor
2. UHT / reflection extractor
3. C++ symbol-lite extractor
4. compile guard extractor
```

---

### 12.3 Build.cs / Module Extractor

输入：

```text
*.Build.cs
*.Target.cs
*.uplugin
*.uproject
```

输出：

```text
Module
Plugin
Target
PublicDependencyModuleNames
PrivateDependencyModuleNames
DynamicallyLoadedModuleNames
PublicIncludePaths
PrivateIncludePaths
Runtime / Editor / Developer / Program / ThirdParty
LoadingPhase
SupportedTargetPlatforms
```

Graph edges：

```text
Plugin --CONTAINS_MODULE--> Module
Module --PUBLIC_DEPENDS_ON--> Module
Module --PRIVATE_DEPENDS_ON--> Module
Module --DYNAMICALLY_LOADS--> Module
Target --USES_MODULE--> Module
```

MVP 可用静态解析，不执行 C#。

---

### 12.4 UHT / Reflection Extractor

输入：

```text
UCLASS(...)
USTRUCT(...)
UENUM(...)
UINTERFACE(...)
UFUNCTION(...)
UPROPERTY(...)
GENERATED_BODY()
GENERATED_UCLASS_BODY()
#include "X.generated.h"
```

抽取 specifiers：

```text
BlueprintCallable
BlueprintPure
BlueprintNativeEvent
BlueprintImplementableEvent
BlueprintReadWrite
BlueprintReadOnly
EditAnywhere
VisibleAnywhere
Replicated
ReplicatedUsing
Config
Transient
SaveGame
meta=(...)
Category=...
```

Graph edges：

```text
CppClass --REFLECTED_AS--> UCLASS
CppFunction --REFLECTED_AS--> UFUNCTION
CppProperty --REFLECTED_AS--> UPROPERTY
UPROPERTY --HAS_SPECIFIER--> ReplicatedUsing
UPROPERTY --REP_NOTIFY_FUNCTION--> OnRep_Function
Header --INCLUDES_GENERATED_HEADER--> GeneratedHeader
```

---

### 12.5 C++ Symbol-lite Extractor

MVP 目标不是完整 clang 级语义，而是稳定解决：

```text
codalith_lookup_symbol("AActor")
codalith_lookup_symbol("TArray")
codalith_lookup_symbol("GetLifetimeReplicatedProps")
```

第一版抽取：

```text
namespace
class
struct
enum
function
method
macro
delegate declaration
console variable hint
```

输出：

```text
symbol_id
name
qualified_name
kind
module
file_uri
declaration_uri
definition_uri candidates
signature
build_guard
confidence
```

后续增强：

```text
tree-sitter-cpp
compile_commands.json
clangd-indexer
libclang
USR-based references
overrides
call hints
```

---

### 12.6 Compile Guard Extractor

必须识别：

```text
WITH_EDITOR
WITH_EDITORONLY_DATA
WITH_SERVER_CODE
UE_BUILD_SHIPPING
UE_BUILD_DEVELOPMENT
PLATFORM_WINDOWS
PLATFORM_LINUX
PLATFORM_PS5
WITH_CHAOS
WITH_NANITE
```

输出：

```json
{
  "file_uri": "ue://5.7.4/source/...",
  "start_line": 120,
  "end_line": 220,
  "guard_expr": "WITH_EDITOR && !UE_BUILD_SHIPPING"
}
```

Context Pack 中如果证据命中 guarded region，必须返回 caveat。

---

## 13. Knowledge Cards 与 CLAUDE.md 定位

### 13.1 Card 类型

```text
ModuleCard
SymbolCard
MechanismCard
RecipeCard
VersionDiffCard
DebugCard
```

### 13.2 第一批种子 Cards

```text
Modules:
  Core
  CoreUObject
  Engine
  NetCore
  RenderCore
  Renderer
  RHI
  Slate
  SlateCore
  UnrealEd

Mechanisms:
  UObject Lifecycle
  UObject Garbage Collection
  UHT Reflection
  UPROPERTY Metadata
  UPROPERTY ReplicatedUsing
  Actor Replication
  RPC Dispatch
  Actor BeginPlay Lifecycle
  ActorComponent Registration
  Build.cs Module Dependency
```

### 13.3 Card Schema

```yaml
id: ue://5.7.4/card/mechanism/actor-replication
type: mechanism_card
version: "5.7.4"
title: Actor Replication

scope:
  modules:
    - Engine
    - NetCore
  symbols:
    - AActor
    - UNetDriver
    - UActorChannel

claims:
  - id: c1
    text: Actor replication is coordinated through Engine networking code involving network drivers, actor channels, and per-actor replication state.
    evidence:
      - ue://5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h#L...
      - ue://5.7.4/source/Engine/Source/Runtime/Engine/Private/DataChannel.cpp#L...
    confidence: high

related_cards:
  - ue://5.7.4/card/mechanism/property-replication
  - ue://5.7.4/card/mechanism/rpc-dispatch

verification:
  status: verified
  source_hashes:
    Engine/Source/Runtime/Engine/Private/DataChannel.cpp: sha256...
```

### 13.4 Verifier 规则

```text
1. 每个 claim 必须有 evidence。
2. evidence URI 必须能 resolve。
3. source file 必须存在。
4. line range 必须有效。
5. symbols 必须存在于 semantic DB。
6. modules 必须存在于 module graph。
7. source hash 必须匹配。
8. evidence 过期时 card 标记 stale。
```

### 13.5 CLAUDE.md 生成

Verified card 渲染到：

```text
UE_KNOWLEDGE/Modules/CoreUObject.md
UE_KNOWLEDGE/Mechanisms/Actor_Replication.md
UE_KNOWLEDGE/Symbols/AActor.md
```

这些 Markdown 会被 CodeRAG 索引，但它们仍然只是解释层，不是事实源。

---

## 14. Context Compiler

### 14.1 输入

```json
{
  "query": "UPROPERTY ReplicatedUsing 是怎么触发 OnRep 的？",
  "version": "5.7.4",
  "project": "ProjectA",
  "mode": "trace",
  "max_source_spans": 8,
  "include_project_overlay": true
}
```

### 14.2 内部流程

```text
1. Resolve corpus
   - engine corpus
   - optional project corpus

2. Classify intent
   - explain
   - trace
   - implement
   - debug
   - api_usage
   - compare

3. Detect UE entities
   - identifiers
   - modules
   - symbols
   - reflection terms
   - build terms
   - mechanisms
   - project symbols

4. Candidate retrieval via CodeRAG
   - semantic query
   - literal query
   - identifier query
   - path/module scoped query
   - UE_KNOWLEDGE card query

5. UE graph expansion
   - symbol → file → module
   - UPROPERTY → reflection metadata
   - ReplicatedUsing → OnRep function
   - class → generated header
   - module → dependencies
   - project symbol → engine mechanism

6. Rerank
   - combine CodeRAG score + UE graph score
   - prefer exact symbol hits
   - prefer verified cards
   - prefer implementation source for trace questions
   - prefer examples for usage questions
   - prefer project overlay for project debug questions

7. Select evidence
   - choose source spans
   - cap source lines
   - attach guard caveats
   - attach version/source_commit

8. Build Context Pack
```

### 14.3 Reranking 公式 v0

```text
final_score =
  0.25 * coderag_score
+ 0.20 * exact_symbol_score
+ 0.15 * module_graph_score
+ 0.15 * reflection_graph_score
+ 0.10 * verified_card_score
+ 0.10 * project_overlay_score
+ 0.05 * version_match_score
- 0.20 * wrong_version_penalty
- 0.15 * editor_only_mismatch_penalty
- 0.10 * declaration_only_when_implementation_needed_penalty
```

### 14.4 Mode 权重调整

```text
mode=explain:
  verified_card_score ↑
  module_graph_score ↑

mode=trace:
  implementation source ↑
  graph proximity ↑

mode=api_usage:
  examples ↑
  public headers ↑

mode=debug:
  project overlay ↑
  exact error/log text ↑
  config/build files ↑

mode=compare:
  version diff ↑
  changed symbols ↑
```

---

## 15. Context Pack Schema

`codalith_context` 返回结构化 Context Pack。MCP tool result 应尽量使用 structured content，同时保留短文本摘要以兼容不同 host。

```json
{
  "schema_version": "0.1",
  "query": "UPROPERTY ReplicatedUsing 是怎么触发 OnRep 的？",
  "version": "5.7.4",
  "source_commit": "abc123",
  "project": "ProjectA",
  "intent": "trace",
  "confidence": "medium_high",

  "answer_policy": {
    "version_pinned": true,
    "must_cite_source": true,
    "do_not_answer_from_memory": true
  },

  "summary": {
    "text": "This context pack identifies reflection, replication, and actor channel code paths related to ReplicatedUsing.",
    "generated_by": "codalith"
  },

  "modules": [
    {
      "name": "Engine",
      "uri": "ue://5.7.4/module/Engine",
      "reason": "Actor replication implementation is primarily in Engine runtime networking code."
    },
    {
      "name": "CoreUObject",
      "uri": "ue://5.7.4/module/CoreUObject",
      "reason": "Owns UObject reflection and property metadata infrastructure."
    }
  ],

  "symbols": [
    {
      "name": "UPROPERTY",
      "uri": "ue://5.7.4/reflection/specifier/UPROPERTY",
      "kind": "reflection_specifier",
      "reason": "ReplicatedUsing is declared through UPROPERTY specifiers."
    },
    {
      "name": "AActor::GetLifetimeReplicatedProps",
      "uri": "ue://5.7.4/symbol/AActor.GetLifetimeReplicatedProps",
      "kind": "method",
      "reason": "Declares replicated properties for actor classes."
    }
  ],

  "cards": [
    {
      "uri": "ue://5.7.4/card/mechanism/uprop-replicated-using",
      "title": "UPROPERTY ReplicatedUsing",
      "verification_status": "verified"
    }
  ],

  "source_spans": [
    {
      "uri": "ue://5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h#L100-L220",
      "path": "Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h",
      "start_line": 100,
      "end_line": 220,
      "reason": "Actor replication-related declarations.",
      "source": "coderag+semantic",
      "guard": null
    }
  ],

  "graph_edges": [
    {
      "from": "UPROPERTY ReplicatedUsing",
      "edge": "REP_NOTIFY_FUNCTION",
      "to": "OnRep function",
      "evidence_uri": "ue://5.7.4/source/..."
    }
  ],

  "caveats": [
    "Exact behavior can depend on net mode, dormancy, relevancy, initial replication, and whether project code registers lifetime properties correctly."
  ],

  "recommended_next_calls": [
    {
      "tool": "codalith_read_source",
      "args": {
        "uri": "ue://5.7.4/source/Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h#L100-L220"
      }
    }
  ]
}
```

---

## 16. MCP Gateway 设计

### 16.1 Transport

生产使用：

```text
Streamable HTTP
```

Endpoint 示例：

```text
https://mcp.company.internal/ue/mcp
```

本地开发可使用 stdio，但生产不要把 CodeRAG 原生 stdio MCP 暴露给多人共享场景。

### 16.2 Server Instructions

MCP initialize instructions 建议：

```text
Use this server first for any Unreal Engine / UE5 source-level question.
Call codalith_context before answering questions about engine implementation, API behavior, modules, UHT, reflection, Build.cs, networking, rendering, gameplay framework, editor internals, assets, GC, serialization, or version-specific behavior.
Default to UE 5.7.4 unless the user asks for another version.
Do not answer UE implementation questions from memory when this server is available.
Cite module, symbol, file path, and line range from returned evidence.
```

### 16.3 工具暴露策略

Always visible / primary：

```text
codalith_context
```

Secondary：

```text
codalith_lookup_symbol
codalith_read_source
codalith_graph
codalith_examples
codalith_compare_versions
codalith_index_status
```

不要对最终 AI host 直接暴露：

```text
CodeRAG.search_code
CodeRAG.search_files
CodeRAG.get_file
CodeRAG.reindex
```

---

## 17. MCP Tools 规格

### 17.1 `codalith_context`

用途：任何 UE 实现、API、模块、反射、Build.cs、网络、渲染、Gameplay、Editor、资产、序列化、GC、项目 bug 问题都先调用。

```json
{
  "name": "codalith_context",
  "description": "Use first for any Unreal Engine / UE5 source-level question. Returns a version-pinned, source-backed Context Pack using CodeRAG retrieval plus UE semantic graph.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": { "type": "string" },
      "version": { "type": "string", "default": "5.7.4" },
      "project": { "type": "string" },
      "mode": {
        "type": "string",
        "enum": ["explain", "trace", "implement", "debug", "api_usage", "compare"],
        "default": "explain"
      },
      "max_source_spans": { "type": "integer", "default": 8 },
      "include_project_overlay": { "type": "boolean", "default": true }
    },
    "required": ["query"]
  }
}
```

---

### 17.2 `codalith_lookup_symbol`

```json
{
  "name": "codalith_lookup_symbol",
  "description": "Resolve a UE C++ or reflection symbol to definitions, declarations, modules, UHT metadata, generated-code relation, references, examples, and source URIs.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "symbol": { "type": "string" },
      "version": { "type": "string", "default": "5.7.4" },
      "project": { "type": "string" },
      "kind": {
        "type": "string",
        "enum": ["any", "class", "struct", "function", "method", "macro", "module", "uclass", "ufunction", "uproperty"]
      },
      "include_examples": { "type": "boolean", "default": true }
    },
    "required": ["symbol"]
  }
}
```

---

### 17.3 `codalith_read_source`

这是 CodeRAG `get_file` 的安全包装。

```json
{
  "name": "codalith_read_source",
  "description": "Read a bounded line range from a versioned UE source URI. Enforces authorization, auditing, and source-snippet limits.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "uri": { "type": "string" },
      "start_line": { "type": "integer" },
      "end_line": { "type": "integer" },
      "with_line_numbers": { "type": "boolean", "default": true }
    },
    "required": ["uri"]
  }
}
```

强制策略：

```text
默认最多 200 行。
硬上限 500 行。
禁止整文件 dump。
所有读取审计。
所有读取经过 source policy。
```

---

### 17.4 `codalith_graph`

```json
{
  "name": "codalith_graph",
  "description": "Return UE graph neighbors for modules, plugins, C++ symbols, reflection entities, Build.cs dependencies, include edges, overrides, generated-code relations, and usage examples.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "node": { "type": "string" },
      "version": { "type": "string", "default": "5.7.4" },
      "project": { "type": "string" },
      "edge_types": {
        "type": "array",
        "items": { "type": "string" }
      },
      "depth": { "type": "integer", "default": 1 },
      "max_nodes": { "type": "integer", "default": 80 }
    },
    "required": ["node"]
  }
}
```

---

### 17.5 `codalith_examples`

```json
{
  "name": "codalith_examples",
  "description": "Find real usages of a UE API or symbol in Engine source, plugins, tests, samples, and project overlay.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "symbol_or_api": { "type": "string" },
      "version": { "type": "string", "default": "5.7.4" },
      "project": { "type": "string" },
      "scope": {
        "type": "string",
        "enum": ["engine", "plugins", "tests", "project", "all"],
        "default": "all"
      },
      "max_examples": { "type": "integer", "default": 8 }
    },
    "required": ["symbol_or_api"]
  }
}
```

---

### 17.6 `codalith_compare_versions`

```json
{
  "name": "codalith_compare_versions",
  "description": "Compare a UE symbol, module, file, or mechanism across engine versions using CodeRAG retrieval and UE semantic diff.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "target": { "type": "string" },
      "from_version": { "type": "string" },
      "to_version": { "type": "string" },
      "diff_type": {
        "type": "string",
        "enum": ["summary", "api", "source", "module_deps", "reflection", "behavior"]
      }
    },
    "required": ["target", "from_version", "to_version"]
  }
}
```

---

### 17.7 `codalith_index_status`

```json
{
  "name": "codalith_index_status",
  "description": "Report CodeRAG index status plus UE semantic extractor status for a UE version or project corpus.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "version": { "type": "string" },
      "project": { "type": "string" }
    }
  }
}
```

---

## 18. MCP Resources 规格

### 18.1 Resources 原则

Resources 用于可引用上下文，不用于批量源码枚举。

只 list 高价值资源：

```text
ue://5.7.4
ue://5.7.4/modules
ue://5.7.4/plugins
ue://5.7.4/cards
ue://5.7.4/mechanisms
ue-project://ProjectA
```

源码文件通过 resource template 或 `codalith_read_source` 读取。

### 18.2 Resource Templates

```json
[
  {
    "uriTemplate": "ue://{version}/module/{module}",
    "name": "UE module"
  },
  {
    "uriTemplate": "ue://{version}/symbol/{symbol}",
    "name": "UE symbol"
  },
  {
    "uriTemplate": "ue://{version}/source/{path}",
    "name": "UE source file"
  },
  {
    "uriTemplate": "ue://{version}/card/{card_type}/{card_id}",
    "name": "UE knowledge card"
  }
]
```

---

## 19. Project Overlay

### 19.1 目标

Project Overlay 使 AI 能回答：

```text
不是“UE 怎么做”，而是“我们项目为什么这样出问题”。
```

### 19.2 Project Corpus

```text
/srv/codalith/corpora/project-a/
  Source/
  Plugins/
  Config/
  ProjectA.uproject
  UE_KNOWLEDGE_PROJECT/
```

### 19.3 Query 流程

用户问：

```text
我们项目的 UInventoryComponent 为什么 OnRep_Items 没触发？
```

Context Compiler：

```text
1. 在 ProjectA corpus 查 UInventoryComponent。
2. 查 Items 是否 UPROPERTY ReplicatedUsing。
3. 查 OnRep_Items 签名。
4. 查 GetLifetimeReplicatedProps。
5. 查 owner Actor 是否 replicated。
6. 查 component 是否 SetIsReplicated / SetIsReplicatedByDefault。
7. 查 UE 5.7.4 ActorComponent / ActorChannel / property replication path。
8. 返回 project source + engine source Context Pack。
```

### 19.4 Project Overlay 第一版支持

```text
- Project CodeRAG index。
- Project Build.cs extractor。
- Project UHT/reflection extractor。
- Project symbol-lite extractor。
- Project → Engine graph edges。
```

---

## 20. 安全、授权与审计

### 20.1 安全原则

UE 源码服务必须视为内部源码系统。

```text
- 不允许匿名访问。
- 不对公网裸露。
- 不提供批量源码导出。
- 不直接暴露 CodeRAG 原生 API。
- 所有 source read 审计。
- 通过 scope 控制 UE 版本、项目、平台、第三方代码访问。
```

### 20.2 Scope 示例

```text
source:read
index:status
index:reindex

ue:5.7
ue:5.8

project:ProjectA
project:ProjectB

platform:win64
platform:linux
platform:console
platform:nda

thirdparty:read
cards:read
graph:read
```

### 20.3 Source Policy

```yaml
limits:
  default_max_lines: 200
  hard_max_lines: 500
  max_source_reads_per_10min: 100
  max_total_lines_per_10min: 10000

deny_patterns:
  - "Engine/Source/ThirdParty/ConsoleSDK/**"
  - "Engine/Platforms/PS5/**"
  - "Engine/Platforms/XSX/**"

sensitive_patterns:
  - pattern: "Engine/Source/ThirdParty/**"
    required_scope: "thirdparty:read"

  - pattern: "Engine/Platforms/**"
    required_scope: "platform:read"
```

### 20.4 Audit Log

每次 `codalith_read_source` 记录：

```json
{
  "timestamp": "2026-06-30T00:00:00Z",
  "user_id": "user-123",
  "session_id": "session-456",
  "tool": "codalith_read_source",
  "uri": "ue://5.7.4/source/...#L100-L220",
  "corpus_id": "ue-5.7.4",
  "path": "Engine/Source/...",
  "start_line": 100,
  "end_line": 220,
  "line_count": 121,
  "client": "claude-code",
  "decision": "allowed"
}
```

---

## 21. 索引管线

### 21.1 Pipeline Overview

```text
Stage 0: Source Snapshot
  - 拉取 UE 源码。
  - 记录 source commit。
  - 固化到 /srv/ue/<version>。
  - 写入 Corpus Registry。

Stage 1: Prepare Indexed Root
  - symlink Engine / Plugins / Programs。
  - 写入 UE_KNOWLEDGE。
  - 应用 ignore rules。
  - 过滤不可访问平台目录。

Stage 2: CodeRAG Index
  - CODERAG_INDEX_ALL_TEXT=1。
  - index source + cards + configs。
  - 生成 vector/BM25 index。

Stage 3: UE Semantic Extractors
  - Build.cs / Target.cs / uplugin。
  - C++ symbol scan。
  - UHT macro/reflection scan。
  - compile guard scan。
  - generated code mapping。

Stage 4: Graph Build
  - module graph。
  - symbol graph。
  - reflection graph。
  - file/module ownership。
  - usage edges。

Stage 5: Knowledge Cards
  - Generate。
  - Verify。
  - Render Markdown。
  - Write UE_KNOWLEDGE。

Stage 6: CodeRAG Reindex Cards
  - incremental reindex。
  - cards become searchable。

Stage 7: Eval
  - run UE eval dataset。
  - publish report。
  - mark corpus ready。
```

### 21.2 命令草图

```bash
python jobs/index_engine.py \
  --version 5.7.4 \
  --source-root /srv/ue/5.7.4 \
  --full

python jobs/generate_cards.py \
  --version 5.7.4 \
  --card-set core

python jobs/verify_cards.py \
  --version 5.7.4

python jobs/publish_corpus.py \
  --version 5.7.4

python jobs/run_eval.py \
  --version 5.7.4 \
  --dataset eval/datasets/ue50.jsonl
```

---

## 22. 数据库模型

MVP 使用 PostgreSQL 即可。图谱先用 edge table，不急于引入 Neo4j。

### 22.1 corpora

```sql
CREATE TABLE corpora (
  corpus_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  ue_version TEXT,
  source_commit TEXT,
  indexed_root TEXT NOT NULL,
  coderag_store TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  indexed_at TIMESTAMPTZ
);
```

### 22.2 source_files

```sql
CREATE TABLE source_files (
  corpus_id TEXT NOT NULL,
  file_uri TEXT PRIMARY KEY,
  relative_path TEXT NOT NULL,
  extension TEXT,
  language TEXT,
  sha256 TEXT NOT NULL,
  size_bytes BIGINT,
  line_count INT,
  module_name TEXT,
  plugin_name TEXT,
  is_public_header BOOLEAN DEFAULT FALSE,
  is_private_source BOOLEAN DEFAULT FALSE,
  is_generated BOOLEAN DEFAULT FALSE,
  is_editor_only BOOLEAN DEFAULT FALSE,
  indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 22.3 ue_modules

```sql
CREATE TABLE ue_modules (
  corpus_id TEXT NOT NULL,
  module_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  module_type TEXT,
  plugin_name TEXT,
  root_path TEXT,
  public_path TEXT,
  private_path TEXT,
  build_cs_uri TEXT,
  UNIQUE(corpus_id, name)
);
```

### 22.4 ue_module_deps

```sql
CREATE TABLE ue_module_deps (
  corpus_id TEXT NOT NULL,
  from_module TEXT NOT NULL,
  to_module TEXT NOT NULL,
  dep_kind TEXT NOT NULL,
  evidence_uri TEXT NOT NULL,
  metadata JSONB DEFAULT '{}',
  PRIMARY KEY(corpus_id, from_module, to_module, dep_kind)
);
```

### 22.5 ue_symbols

```sql
CREATE TABLE ue_symbols (
  corpus_id TEXT NOT NULL,
  symbol_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  qualified_name TEXT,
  kind TEXT NOT NULL,
  module_name TEXT,
  file_uri TEXT,
  declaration_uri TEXT,
  definition_uri TEXT,
  signature TEXT,
  namespace TEXT,
  class_owner TEXT,
  is_template BOOLEAN DEFAULT FALSE,
  is_virtual BOOLEAN DEFAULT FALSE,
  is_override BOOLEAN DEFAULT FALSE,
  is_static BOOLEAN DEFAULT FALSE,
  build_guard TEXT,
  confidence REAL DEFAULT 1.0
);
```

### 22.6 ue_reflection_entities

```sql
CREATE TABLE ue_reflection_entities (
  corpus_id TEXT NOT NULL,
  reflection_id TEXT PRIMARY KEY,
  cpp_symbol_id TEXT,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  owner_symbol_id TEXT,
  module_name TEXT,
  declaration_uri TEXT,
  generated_uri TEXT,
  specifiers JSONB DEFAULT '{}',
  metadata JSONB DEFAULT '{}',
  confidence REAL DEFAULT 1.0
);
```

### 22.7 codalith_graph_edges

```sql
CREATE TABLE codalith_graph_edges (
  corpus_id TEXT NOT NULL,
  edge_id TEXT PRIMARY KEY,
  from_node TEXT NOT NULL,
  to_node TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  evidence_uri TEXT,
  extractor TEXT NOT NULL,
  confidence REAL DEFAULT 1.0,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_codalith_graph_from
ON codalith_graph_edges(corpus_id, from_node, edge_type);

CREATE INDEX idx_codalith_graph_to
ON codalith_graph_edges(corpus_id, to_node, edge_type);
```

### 22.8 knowledge_cards

```sql
CREATE TABLE knowledge_cards (
  corpus_id TEXT NOT NULL,
  card_id TEXT PRIMARY KEY,
  card_type TEXT NOT NULL,
  title TEXT NOT NULL,
  version TEXT,
  body_markdown TEXT NOT NULL,
  claims JSONB NOT NULL,
  evidence JSONB NOT NULL,
  related_nodes JSONB DEFAULT '[]',
  source_hashes JSONB DEFAULT '{}',
  verification_status TEXT NOT NULL,
  generated_by TEXT,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 23. 配置文件

### 23.1 `configs/corpus_registry.yaml`

见第 9 节。

### 23.2 `configs/source_policy.yaml`

见第 20 节。

### 23.3 CodeRAG 环境变量示例

```bash
export CODERAG_WATCHED_DIR=/srv/codalith/corpora/ue-5.7.4
export CODERAG_STORE_DIR=/var/lib/codalith/coderag/ue-5.7.4
export CODERAG_INDEX_ALL_TEXT=1
export CODERAG_MODEL=BAAI/bge-small-en-v1.5
export CODERAG_RERANK=0
```

### 23.4 MCP Server Config

```yaml
server:
  name: codalith
  transport: streamable-http
  endpoint: /ue/mcp
  default_version: "5.7.4"

tools:
  primary:
    - codalith_context
  secondary:
    - codalith_lookup_symbol
    - codalith_read_source
    - codalith_graph
    - codalith_examples
    - codalith_compare_versions
    - codalith_index_status
```

---

## 24. 客户端接入策略

### 24.1 Claude Code

```bash
claude mcp add --transport http codalith https://mcp.company.internal/ue/mcp \
  --header "Authorization: Bearer $UE_MCP_TOKEN"
```

项目根目录增加：

```text
CLAUDE.md
```

内容：

```markdown
# UE Source Grounding Policy

When answering Unreal Engine implementation questions:

1. Call `codalith_context` first.
2. Use UE 5.7.4 unless the user asks for another version.
3. Do not rely on generic memory when source-backed Codalith is available.
4. Cite module, symbol, file path, and line range.
5. Distinguish Engine source, project source, plugins, generated code, editor-only code, and platform-specific code.
6. For UCLASS/USTRUCT/UFUNCTION/UPROPERTY questions, inspect reflection metadata.
7. For Build.cs questions, inspect module dependencies.
8. For project bugs, include both project overlay and engine source evidence.
```

### 24.2 Codex

`.codex/config.toml` 示例：

```toml
[mcp_servers.codalith]
url = "https://mcp.company.internal/ue/mcp"
bearer_token_env_var = "UE_MCP_TOKEN"
tool_timeout_sec = 60
startup_timeout_sec = 10
enabled = true
default_tools_approval_mode = "approve"
enabled_tools = [
  "codalith_context",
  "codalith_lookup_symbol",
  "codalith_read_source",
  "codalith_graph",
  "codalith_examples",
  "codalith_compare_versions",
  "codalith_index_status"
]
```

### 24.3 Cursor

`.cursor/mcp.json` 示例：

```json
{
  "mcpServers": {
    "codalith": {
      "url": "https://mcp.company.internal/ue/mcp",
      "headers": {
        "Authorization": "Bearer ${env:UE_MCP_TOKEN}"
      }
    }
  }
}
```

---

## 25. 评估体系

### 25.1 评估分层

```text
Layer 1: CodeRAG retrieval eval
  - file_recall@5
  - MRR
  - nDCG@10
  - latency

Layer 2: UE semantic eval
  - module_accuracy
  - symbol_accuracy
  - reflection_entity_accuracy
  - build_dependency_accuracy
  - guard_detection_accuracy

Layer 3: Context Pack eval
  - evidence_coverage
  - source_span_accuracy
  - unsupported_claim_rate
  - wrong_version_rate
  - missing_source_citation_rate

Layer 4: Agent answer eval
  - final answer correctness
  - source citation usage
  - project/engine distinction
  - editor/runtime distinction
```

### 25.2 ue50 数据集示例

```json
{
  "id": "ue-core-001",
  "query": "UObject GC 主要实现在哪里？",
  "version": "5.7.4",
  "relevant_files": [
    "Engine/Source/Runtime/CoreUObject/Private/UObject/GarbageCollection.cpp"
  ],
  "expected_modules": [
    "CoreUObject"
  ],
  "expected_symbols": [
    "CollectGarbage"
  ]
}
```

### 25.3 第一批 50 题类别

```text
Core / CoreUObject: 5
Reflection / UHT: 5
Build: 5
Gameplay: 5
Networking: 5
Rendering: 5
Editor: 5
Project Overlay: 5
Version Compare: 5
Mixed systems: 5
```

### 25.4 关键指标目标

```text
file_recall@5 >= 0.70   # PoC 阶段
file_recall@5 >= 0.85   # Semantic Layer 加入后
symbol_recall@10 >= 0.80
module_accuracy >= 0.90
unsupported_claim_rate < 0.10
wrong_version_rate < 0.03
missing_source_citation_rate < 0.05
```

---

## 26. 实施路线

### Phase 0：CodeRAG UE PoC

交付：

```text
- CodeRAG 索引 UE 5.7.x。
- 50 个 UE 检索问题。
- latency / index size / recall@5 报告。
- 是否启用 code embedding / reranker 的数据结论。
```

通过标准：

```text
file_recall@5 >= 0.70
get_file 可用
索引过程可复现
```

---

### Phase 1：Codalith Gateway v0

交付：

```text
- MCP Streamable HTTP Gateway。
- auth 基础版。
- audit log。
- corpus registry。
- URI resolver。
- CodeRAGAdapter。
- codalith_context v0。
- codalith_read_source。
- codalith_index_status。
```

`codalith_context v0` 可以先只做：

```text
query
  → CodeRAG search_code
  → CodeRAG literal searches
  → simple rerank
  → Context Pack v0
```

---

### Phase 2：Build.cs / Module Graph

交付：

```text
- Build.cs parser。
- Target.cs parser。
- uplugin parser。
- module table。
- module dependency edge table。
- codalith_graph basic。
- ModuleCard。
- module graph eval。
```

---

### Phase 3：UHT / Reflection Graph

交付：

```text
- UCLASS/USTRUCT/UENUM parser。
- UFUNCTION/UPROPERTY parser。
- metadata/specifier extraction。
- generated.h include relation。
- reflection entity table。
- MechanismCards。
```

---

### Phase 4：C++ Symbol Index

交付：

```text
- C++ class/function/method/macro scan。
- codalith_lookup_symbol。
- examples finder。
- public/private header relationship。
- source definition / declaration pairing。
```

---

### Phase 5：Project Overlay

交付：

```text
- Project corpus。
- Project semantic extractors。
- project symbols。
- project reflection metadata。
- project module graph。
- project-to-engine graph edges。
- project debug Context Pack。
```

---

### Phase 6：Version Diff

交付：

```text
- 多 UE 版本 corpus。
- symbol diff。
- source diff。
- module dep diff。
- reflection diff。
- VersionDiffCard。
- codalith_compare_versions。
```

---

## 27. 第一版验收标准

### 27.1 CodeRAG PoC 验收

```text
- UE 5.7.x 源码能完整索引成功。
- 索引过程可重复。
- index_status 能报告文件数、chunk 数、模型、更新时间。
- 50 题 file_recall@5 >= 0.70。
- 查询 p95 延迟可接受。
- get_file 能稳定读取 path + line range。
```

### 27.2 Codalith v0 验收

```text
- codalith_context 能返回 Context Pack。
- Context Pack 包含 version、modules、symbols、source_spans、cards、caveats。
- codalith_read_source 强制行数限制。
- 所有 source read 有 audit log。
- 未授权 corpus 访问被拒绝。
- AI 客户端里 UE 问题会优先看到 codalith_context。
```

### 27.3 Semantic v0 验收

```text
- 至少解析 100 个核心模块的 Build.cs。
- 能查询 public/private module dependency。
- 能识别 UCLASS / UFUNCTION / UPROPERTY。
- 能识别 ReplicatedUsing → OnRep 函数名。
- 能识别 WITH_EDITOR / UE_BUILD_SHIPPING guard。
- 能 lookup 100 个核心 symbol。
```

### 27.4 Cards v0 验收

```text
- 20 张核心 Knowledge Cards。
- 每张 card verified。
- 每个 claim 至少一个 evidence URI。
- card Markdown 被写入 UE_KNOWLEDGE。
- CodeRAG 能检索到 card。
- card evidence 能回读源码。
```

---

## 28. 风险与缓解

### 28.1 CodeRAG 对 UE C++ 召回不够准

原因：

```text
C/C++ 当前主要走 line-window fallback。
```

缓解：

```text
1. 加 Knowledge Cards。
2. 加 exact identifier search。
3. 加 C++ symbol-lite extractor。
4. 给 CodeRAG indexed root 加 generated symbol docs。
5. 后续接 tree-sitter-cpp 或 clangd-indexer。
```

### 28.2 UE 源码规模导致索引慢或过大

缓解：

```text
1. 首轮只索引 Engine/Source + 高价值 Engine/Plugins。
2. ThirdParty 默认排除。
3. Intermediate 默认排除。
4. 分 corpus：ue-core、ue-rendering、ue-editor、ue-plugins。
5. CodeRAG store 按 corpus 分开。
```

### 28.3 AI 仍然不调用 MCP

缓解：

```text
1. server instructions 第一行写 Use first。
2. 工具名使用 codalith_context，不使用 generic search。
3. 项目 CLAUDE.md / AGENTS.md 写 grounding policy。
4. 只把 codalith_context 作为核心入口。
5. 其他工具延迟暴露。
```

### 28.4 源码泄露

缓解：

```text
1. 不暴露 CodeRAG 原生 HTTP API。
2. Gateway 做 auth/RBAC/audit。
3. codalith_read_source 行数限制。
4. rate limit。
5. deny sensitive path。
6. bulk export detection。
```

### 28.5 Knowledge Cards 变成幻觉缓存

缓解：

```text
1. 每个 claim 必须有 evidence。
2. verifier 校验 URI、line range、symbol、module、hash。
3. source hash 变化，card 自动 stale。
4. card 不作为事实源，只作为检索和解释层。
```

---

## 29. 给本地 AI 工具的实现任务拆分

下面任务适合直接交给 Claude Code / Codex / Cursor 分阶段实现。

### Task 1：初始化仓库

```text
创建 pyproject.toml。
创建 src/codalith 基础包结构。
创建 configs 目录。
创建 jobs 目录。
创建 tests 目录。
```

验收：

```text
pytest 可运行。
ruff/mypy 可选。
项目可安装为 editable package。
```

---

### Task 2：实现 Corpus Registry

输入：

```text
configs/corpus_registry.yaml
```

实现：

```text
CorpusRegistry.get_engine(version)
CorpusRegistry.get_project(project)
CorpusRegistry.resolve(version, project, include_project_overlay)
```

验收：

```text
能从 version 解析 corpus_id。
能从 project 解析 project corpus + engine corpus。
```

---

### Task 3：实现 URIResolver

支持：

```text
ue://5.7.4/source/...#L10-L20
ue-project://ProjectA/source/...#L10-L20
```

验收：

```text
正确解析 corpus_id、relative_path、line range。
非法 scheme 抛错。
非法 line fragment 抛错。
```

---

### Task 4：实现 CodeRAGAdapter

封装：

```text
search_code
search_files
get_file
status
```

验收：

```text
可以对一个小型 repo 建索引并搜索。
结果映射成 RetrievalHit。
```

---

### Task 5：实现 SourcePolicy

支持：

```text
max lines
hard max lines
deny patterns
sensitive patterns
user scopes
rate limit stub
```

验收：

```text
超过行数拒绝。
敏感路径缺 scope 拒绝。
普通路径允许。
```

---

### Task 6：实现 codalith_read_source

流程：

```text
uri → resolver → policy → CodeRAG.get_file → audit → result
```

验收：

```text
能读指定行。
能加行号。
能拒绝超限。
有 audit record。
```

---

### Task 7：实现 Build.cs Extractor v0

支持抽取：

```text
PublicDependencyModuleNames
PrivateDependencyModuleNames
DynamicallyLoadedModuleNames
```

验收：

```text
给定样例 Build.cs，能输出 module deps。
能写入 ue_module_deps。
```

---

### Task 8：实现 UHT Reflection Extractor v0

支持：

```text
UCLASS
USTRUCT
UFUNCTION
UPROPERTY
ReplicatedUsing
BlueprintCallable
BlueprintNativeEvent
meta=(...)
generated.h include
```

验收：

```text
给定样例 header，能识别 reflection entities。
能识别 ReplicatedUsing → OnRep。
```

---

### Task 9：实现 ContextCompiler v0

流程：

```text
query → CodeRAG search_code + search_files → simple rerank → ContextPack
```

验收：

```text
codalith_context 返回符合 schema 的 Context Pack。
source_spans 至少包含 URI、path、line range、reason。
```

---

### Task 10：实现 MCP Gateway v0

注册：

```text
codalith_context
codalith_read_source
codalith_index_status
```

验收：

```text
Claude Code / Codex / Cursor 至少一个客户端可连接。
tools/list 有工具。
tools/call 可返回结果。
```

---

### Task 11：实现 Knowledge Card verifier

支持：

```text
claim evidence required
URI exists
line range valid
source hash match
related nodes exist
```

验收：

```text
无 evidence 的 card 失败。
非法 URI 的 card 失败。
合法 card verified。
```

---

### Task 12：实现 eval runner v0

读取：

```text
eval/datasets/ue50.jsonl
```

输出：

```text
file_recall@5
module_accuracy
latency
```

验收：

```text
能生成 JSON/Markdown 报告。
```

---

## 30. 开放问题

```text
1. UE 版本命名以 tag、branch、commit 还是内部构建号为准？
2. 是否需要索引完整 Engine/Plugins，还是先只索引白名单模块？
3. 是否索引 Intermediate generated code？如果索引，按什么 build configuration？
4. Console platform / NDA code 是否完全隔离为独立 corpus？
5. 第一版是否启用 CodeRAG reranker？
6. 是否需要公司内部 SSO/OAuth，还是先用 static bearer token？
7. Project Overlay 是否第一版就接入真实项目，还是先用样例项目？
8. Knowledge Cards 是 AI 自动生成还是人工 seed + AI 补 evidence？
9. Eval 中 expected files 如何维护，是否按版本分开？
10. 最终是否 fork CodeRAG，还是长期保持 wrapper 模式？
```

---

## 31. 参考来源

以下来源用于确定当前方案中关于 CodeRAG、MCP、客户端接入和 UE 源码访问的事实基础：

1. CodeRAG GitHub README  
   https://github.com/Neverdecel/CodeRAG

2. CodeRAG MCP server implementation  
   https://raw.githubusercontent.com/Neverdecel/CodeRAG/master/coderag/surfaces/mcp_server.py

3. CodeRAG language mapping / symbol-aware chunking scope  
   https://raw.githubusercontent.com/Neverdecel/CodeRAG/master/coderag/chunking/languages.py

4. CodeRAG configuration docs  
   https://raw.githubusercontent.com/Neverdecel/CodeRAG/master/docs/configuration.md

5. CodeRAG eval docs  
   https://raw.githubusercontent.com/Neverdecel/CodeRAG/master/docs/eval.md

6. MCP specification 2025-11-25  
   https://modelcontextprotocol.io/specification/2025-11-25

7. MCP Tools specification  
   https://modelcontextprotocol.io/specification/2025-11-25/server/tools

8. MCP Resources specification  
   https://modelcontextprotocol.io/specification/2025-11-25/server/resources

9. MCP Transports specification  
   https://modelcontextprotocol.io/specification/2025-11-25/basic/transports

10. Claude Code MCP documentation  
    https://code.claude.com/docs/en/mcp

11. OpenAI Codex MCP documentation  
    https://developers.openai.com/codex/mcp

12. Cursor MCP documentation  
    https://cursor.com/docs/mcp.md

13. Epic Unreal Engine source access documentation  
    https://dev.epicgames.com/documentation/unreal-engine/downloading-source-code-in-unreal-engine

---

## 附录 A：第一版 ue50 问题清单

```text
Core / CoreUObject
1. UObject 生命周期入口在哪里？
2. UObject GC 主要实现在哪些文件？
3. TArray 扩容逻辑在哪里？
4. FName 比较为什么快？
5. FText 和 FString 的底层差异在哪里看？

Reflection / UHT
6. UCLASS metadata 在哪里被声明和使用？
7. UPROPERTY specifier 如何进入反射数据？
8. BlueprintCallable 相关生成代码在哪里？
9. BlueprintNativeEvent 为什么会生成 _Implementation？
10. generated.h 和原始 header 是怎么关联的？

Build
11. PublicDependencyModuleNames 和 PrivateDependencyModuleNames 的差异在哪里体现？
12. Runtime module 为什么不应该依赖 UnrealEd？
13. uplugin LoadingPhase 在哪里解析？
14. Target.cs 影响哪些构建行为？
15. ModuleRules 的依赖最终在哪里使用？

Gameplay
16. Actor BeginPlay 调用链在哪里？
17. ActorComponent 注册流程在哪里？
18. SpawnActor 主要实现在哪里？
19. UWorld Tick 入口在哪里？
20. GameMode / GameState 生命周期相关代码在哪里？

Networking
21. Actor replication 从哪里开始？
22. UPROPERTY ReplicatedUsing 如何触发 OnRep？
23. RPC dispatch 相关代码在哪里？
24. Dormancy 如何影响 replication？
25. Actor relevancy 相关代码在哪里？

Rendering
26. RDG pass 在哪里执行？
27. RHI 和 Renderer 模块边界在哪里？
28. Shader compile pipeline 入口在哪里？
29. Nanite 主要模块在哪里？
30. Lumen 相关核心代码在哪里？

Editor
31. Details customization 入口在哪里？
32. AssetRegistry scan 相关代码在哪里？
33. UnrealEd 模块依赖哪些 Runtime 模块？
34. Slate widget 声明和构建常见模式在哪里？
35. Editor-only code 如何通过 WITH_EDITOR 隔离？

Project Overlay
36. 项目 OnRep 不触发应该查哪些点？
37. 项目 RPC 不调用应该查哪些点？
38. 项目 Build.cs 缺依赖如何定位？
39. Editor module 代码进 Shipping 报错如何定位？
40. 项目组件 replication 不工作如何定位？

Version
41. 某个 symbol 在 5.7 和 5.8 是否变化？
42. 某个模块依赖在 5.7 和 5.8 是否变化？
43. 某个 UPROPERTY metadata 行为是否版本相关？
44. 某个 rendering API 是否变更？
45. 某个 networking path 是否变更？

Mixed
46. GameplayAbilities 的 ASC replication 相关代码在哪里？
47. Enhanced Input 主要模块和入口在哪里？
48. MassEntity 主要架构在哪里？
49. Niagara system lifecycle 相关代码在哪里？
50. Chaos physics 和 Engine runtime 的边界在哪里？
```

---

## 附录 B：第一版核心文件清单

优先创建：

```text
configs/corpus_registry.yaml
configs/source_policy.yaml
src/codalith/corpus/registry.py
src/codalith/corpus/uri_resolver.py
src/codalith/corpus/source_policy.py
src/codalith/coderag/adapter.py
src/codalith/compiler/context_pack.py
src/codalith/compiler/context_compiler.py
src/codalith/gateway/mcp_server.py
src/codalith/gateway/tools.py
src/codalith/semantic/extractors/build_cs.py
src/codalith/semantic/extractors/uht_reflection.py
src/codalith/cards/schema.py
src/codalith/cards/verifier.py
eval/datasets/ue50.jsonl
```

---

## 附录 C：最小闭环

第一版最小闭环定义：

```text
1. CodeRAG 成功索引 UE 5.7.x indexed root。
2. MCP Gateway 暴露 codalith_context / codalith_read_source / codalith_index_status。
3. codalith_context 内部调用 CodeRAG，返回 Context Pack v0。
4. codalith_read_source 可以按 ue:// URI 读取受限源码范围。
5. SourcePolicy 生效，所有 source read 有 audit。
6. 20 张 verified Knowledge Cards 写入 UE_KNOWLEDGE，并被 CodeRAG 检索。
7. ue50 eval 能生成报告。
8. Claude Code / Codex / Cursor 至少一个客户端能完成端到端查询。
```

这就是本项目的第一个可用版本。
