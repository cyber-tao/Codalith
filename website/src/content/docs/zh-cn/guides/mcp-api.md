---
title: MCP API
description: 七个只读 MCP 工具、规范源码引用、资源契约和固定错误码。
---

两种传输均使用官方 Python MCP SDK 和相同的严格 Pydantic schema。未知输入字段会报错。工具注解声明只读、非破坏、幂等和 closed-world 行为。

一次工具调用会将每个目标语料解析到一个 generation，并在整个调用期间固定使用。源码策略拒绝的路径不会出现在任何输出中，并记录于 `warnings`。

## Agent 工作流与引用

安装 `codalith-source` 技能以指导检索选择和证据边界；MCP 工具 schema 仍是可执行契约。服务初始化时列出已注册的语料 revision、工作空间和默认目标。注册不等于索引已就绪。目标、版本或就绪状态不确定时检查 `codalith_status`，无需在每次请求前重复检查；显式选择所需目标。

上下文文本足够、当前且哈希匹配时，可以直接使用。使用 search/symbol 结果定位，再读取缺少的实现。问题得到回答或需要运行时证据时即可停止。静态调用和引用证据无法枚举运行进程的回调注册、注册历史或执行顺序。

源码引用原样使用返回的 `codalith://<corpus>/source/<path>#Lx-Ly` URI。符号的 `uri` 标识符号，`source_uri` 才定位源码；symbol/status URI 和裸 corpus URI 均不是源码引用。

需要引用其他范围时，通过 `codalith_read` 请求，再引用响应中的实际 URI，不能照抄请求 URI，因为文件结尾可能缩短范围。保留对应 revision/generation 来源，检查 warnings、stale 和 truncation，不要把不同 generation 拼成一个已验证快照。

## 工具

### `codalith_search`

输入：`query`，可选的 `target`、`strategy` 和 `limit`（1–50）。

- `auto`：对候选标识符执行精确符号查找，按名称和路径词查找文件，同时取得语义命中，按排名融合。
- `semantic`：仅语义命中；generation 缺少语义平面时以 `retrieval_failed` 失败。
- `text`：在 generation 的索引文件中对完整 query 做不区分大小写的字面匹配，每个文件最多三行，存在大小写完全匹配的文件优先；还补充 query 中标识符的精确符号证据。不需要语义平面。
- `symbol`：精确符号查找，必要时回退到子串匹配。

返回排序命中，包括 corpus/revision/generation、规范源码 URI、路径和范围、符号元数据、归一化分数、参与后端（`structure`、`semantic`、`text`）、snippet、降级标志和 warnings。某个语料无法提供部分请求能力时，响应标为 `degraded`，而不是让整个响应失败。

### `codalith_context`

输入：`query`，可选的 `target`、`max_spans`（1–20）和 `max_chars`（1,000–100,000）。

返回不重叠的源码片段，包括当前/索引哈希、过期和解码状态、置信度及 warnings。当硬行数上限或字符预算使片段提前结束、未覆盖完整符号、chunk 或文件时，`truncated` 为 true。

### `codalith_read`

输入：一个规范的 `codalith://<corpus>/source/<path>` URI，可带 `#Lx-Ly` 范围。范围必须包含两个边界，单行写为 `#L5-L5`。省略范围时使用源码策略的默认行数上限。此工具不接受 symbol URI。

路径必须存在于当前结构 generation 中。响应包括验证时的 `generation_id`、实际规范范围、总行数、文本、两个哈希和 stale 状态。返回 URI 始终标识实际范围，引用时原样保留。

### `codalith_symbol`

输入：符号 `query`，可选的 `target`、`exact` 和 `limit`（1–100）。返回定义以及规范的 symbol/source URI。

`exact` 对名称或限定名称进行不区分大小写的匹配。`exact=false` 时依次返回完整名称匹配、名称前缀匹配、名称或限定名称子串匹配。每组内定义优先于声明，再按限定名称较短者优先。query 按字面处理，`%`、`_` 和引号没有特殊含义。

### `codalith_graph`

输入：规范 symbol `root_uri`、`direction`、`depth`（1–3）和边 `limit`（1–1,000）。返回带 `generation_id` 的节点、具有证据的边和截断信息。边的 `resolution` 为 `resolved`（精确限定匹配）、`name_match`（最后一段名称恰好匹配语料中的唯一符号）、`ambiguous` 或 `unresolved`。

### `codalith_compare`

输入：`from_corpus`、`to_corpus`、`include_unchanged` 和 `limit`（1–1,000）。返回新增、移除、变化、不变和有歧义的符号分组，以及变化字段。每组每侧最多返回 20 个定义，并显式标记组级截断。

按 comparison key 顺序比较分组，比较签名、路径、模块和元数据。比较在 30 秒后停止，此时 `truncated` 为 true，`warnings` 说明已比较的组数。差异组数超过 `limit` 时也会标记 `truncated`。

### `codalith_status`

输入：可选的 `target`。无需加载 embedding 模型或扫描源码，返回每个语料的状态：`ready`（结构和语义平面均有效）、`degraded`（缺少语义平面）、`missing`（没有已发布 generation）或 `invalid`（当前 generation 与产物、store 来源信息或配置不再一致）。

## 资源

列出的 resources 提供语料状态。Templates 提供索引源码和符号 URI。资源读取返回 JSON，使用与工具相同的服务方法。

## 错误

工具失败返回 MCP error result，其 structured content 为：

```json
{
  "error": {
    "code": "index_missing",
    "message": "human-readable detail",
    "retryable": false,
    "details": {}
  }
}
```

错误码是 `codalith.errors` 中声明的固定契约，不从异常类名推导。资源读取以 JSON content 返回相同 payload。

| 错误码 | 含义 | 可重试 |
| --- | --- | --- |
| `invalid_arguments` | 输入未通过 schema 或范围验证 | 否 |
| `invalid_uri` | `codalith://` URI 格式错误或不规范 | 否 |
| `corpus_not_found` | 未知语料或工作空间 | 否 |
| `symbol_not_found` | 符号 ID 不属于当前 generation | 否 |
| `source_policy` | 路径或行数范围违反源码策略 | 否 |
| `source_unreadable` | 索引文件目前缺失或不可读 | 否 |
| `index_missing` | 语料没有已发布 generation | 否 |
| `index_invalid` | 当前 generation 与产物或配置不匹配 | 否 |
| `index_unavailable` | 目标中没有语料可以提供请求服务 | 否 |
| `retrieval_failed` | 语义或文本检索失败，例如 embedding endpoint 错误 | 是 |
| `configuration_invalid` | Registry 或源码策略无效 | 否 |
| `index_build_failed` | 构建或发布 generation 失败（CLI） | 否 |
| `internal` | 意外故障；日志不含请求体，隐藏内部消息 | 是 |
