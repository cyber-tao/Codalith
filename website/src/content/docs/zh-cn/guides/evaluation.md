---
title: 评测
description: 通过真实 MCP 会话运行检索评测，理解数据集、质量门槛、引用复核和 Agent 对比协议。
---

Benchmark 始终使用真实 MCP Streamable HTTP 会话，不直接调用 `QueryService`，也不接受以成功退出码掩盖的降级替代输出。本页说明评测协议，不代表某次运行已达到下列门槛。

## 数据集

- `sample-smoke.jsonl`：确定性的英文、中文、代码、text、symbol 和负例冒烟用例。
- `ue-regression.jsonl`：迁移的 80 条历史 UE 用例，其中 78 条 `auto`、2 条 `symbol`。
- `ue-holdout.jsonl`：40 条独立选择的 UE 问题，是主要质量信号。
- `ue-literal.jsonl`：只在一个文件中出现的日志宏定义，以及不存在的字面量，单独评估文件检索和引用。
- `ue-semantic.jsonl`：13 条不带符号名或文件名的中英文问题，使用 `semantic` 策略，覆盖音频、动画、物理、HTTP、socket、导航、相机和渲染；仅评估文件检索与引用。

每行 JSONL 使用严格 schema，包含 `id`、`query`、可选 `target`、`strategy`、预期文件/符号、negative 标志、语言和类别。旧字段或未知字段会被拒绝。

Regression 和 holdout 没有负例，也没有仅 `semantic` 或 `text` 的用例；使用独立诊断集覆盖这些策略。语义搜索结合 dense embedding 与 BM25，所以分数反映整个语义检索流程，而非孤立的 embedding 能力。早期 UE regression 曾被编码答案的查询规则抬高；这些规则已移除，新运行应建立新基线，不能直接沿用历史报告比较。

UE 5.7.4 重建前，全部 120 条 regression/holdout 用例及其 95 个不同预期路径，已对照 `5.7.4-release`（`260bb2e1c5610b31c63a36206eedd289409c5f11`）检查。Holdout 的碰撞结构标签从 `EngineTypes.h` 修正到声明该结构的 `HitResult.h`。日志问题保留原措辞并使用 `auto`；其旧 `text` 策略混淆了自然语言问题和字面搜索契约。

独立 literal 诊断使用 `LogMacros.h:255` 中完整的 `UE_LOG_ACTIVE` 宏定义，已通过对固定完整快照进行不区分大小写的固定字符串搜索，验证仅在该文件出现。宽泛的 `UE_LOG` 在许多合法调用点出现，其宏签名甚至也出现在插件中，单一相关文件标签会误判有效命中。字面文本命中不保证符号元数据，因此该诊断无 symbol ground truth，不存在字面量的用例保持不变。

四个 regression 符号标签在检索评测前依据声明修正：`Renderer.cpp:79` 实现 `FRendererModule::StartupModule`；`DelegateSignatureImpl.inl:1222` 实现 `__Internal_AddDynamic`；`TimerManager.h:276` 声明 `ClearTimer`；`Actor.h:273` 声明 `GetLifetimeReplicatedProps`。对应原标签中的类、公共宏、反射宏或参数类型，并非在这些位置声明；复制宏仍定义于 `UnrealNetwork.h:259`。这四条问题和预期文件未改变。修正先于检索评测，不是为了响应 benchmark 失败。公共聚合头和宏生成类型仍是有效标签；adapter 提取失败不能靠修改标签提高分数。

## 指标与默认门槛

| 指标 | 门槛 |
| --- | --- |
| File recall@5 | ≥ 0.85 |
| Symbol recall@5（存在符号标签时） | ≥ 0.80 |
| MRR | ≥ 0.65 |
| nDCG@10 | ≥ 0.75 |
| 引用有效率 | 1.00 |
| 降级率 | 0 |
| 负例通过率（存在负例时） | 1.00 |
| p95 延迟 | ≤ 2,000 ms |
| 错误数 | 0 |

评分前对排序路径去重；符号按不同的 `(path, symbol)` 对排名。符号匹配区分大小写，允许限定名称后缀匹配；指定预期文件时，符号必须属于该文件。失败查询对所有适用质量指标贡献零。

没有对应 ground truth 的指标为 `null`（N/A），不应用质量门槛；这适用于仅文件、仅符号和仅负例数据集。报告不完整、正例未评分，或存在适用行分数却缺少汇总，仍是错误。引用、降级、延迟和错误门槛始终适用。

评分前整个 search 响应，包括每个命中，都必须满足公开 schema。每条前五引用都通过 `codalith_read` 往返验证，URI、corpus、revision、generation、路径和行数范围必须一致，当前与索引 SHA-256 相同且无 stale 标志；评测应针对稳定的 generation 运行。延迟测量 search 调用和 schema 验证，不包含成功引用复核。比较部署性能时，应另行测量冷启动和多次热运行。

## 命令

```bash
uv run codalith benchmark \
  --endpoint-url http://127.0.0.1:8765/mcp \
  --dataset benchmarks/datasets/sample-smoke.jsonl \
  --label sample \
  --output reports/sample.json

uv run codalith benchmark \
  --endpoint-url http://127.0.0.1:8765/mcp \
  --dataset benchmarks/datasets/ue-regression.jsonl \
  --label ue-regression \
  --output reports/ue-regression.json

uv run codalith benchmark \
  --endpoint-url http://127.0.0.1:8765/mcp \
  --dataset benchmarks/datasets/ue-holdout.jsonl \
  --label ue-holdout \
  --output reports/ue-holdout.json
```

Regression 和 holdout 分开运行。检索失败时先诊断 adapter、index 和 query 行为，不应据此修改 holdout 标签；真正新增的用例可以加入，但不能替换失败用例。

按相同命令形式分别运行 `ue-literal.jsonl` 与 `ue-semantic.jsonl`，使用独立输出路径。Semantic 诊断领域未参与模型选择探测。C++ semantic chunking 当前使用无符号元数据的行窗口，`strategy=semantic` 不执行结构符号检索。因此这些用例保留基于源码的文件标签，无 symbol ground truth，symbol recall 为 N/A；此设计在 UE 检索运行前按流程契约确定。

小型诊断集不能代替 holdout，也不能建立通用模型质量排名。在本地报告旁保存源码 commit、generation manifest、模型标识与维度、数据集哈希及冷/热运行条件。

检索代码不得编码评测问题、关键词或预期答案路径。语言和引擎惯例仅通过 `codalith.languages.catalog` 中 adapter 的 `RetrievalProfile` 进入查询层。每个排名变化都应以未见问题上的行为支持。Regression 用于回归保护，holdout 用作质量信号。

## Agent 工作流对比

`agent-benchmark` 对比仅使用 MCP 的 Agent，与可按需发现和读取 `skills/codalith-source/SKILL.md` 的 Agent。两组使用相同本地 Ollama 模型、服务指令、七个 MCP 工具、独立对话历史、输出契约和 MCP 调用预算。技能组额外获得技能名称、描述及 `read_skill` 工具；加载正文消耗模型轮次，并计入 token 与总耗时。

```bash
uv run codalith agent-benchmark \
  --endpoint-url http://127.0.0.1:8765/mcp \
  --model-url http://127.0.0.1:11434 \
  --model qwen3.8:latest \
  --dataset benchmarks/datasets/ue-agent-tasks.jsonl \
  --skill skills/codalith-source/SKILL.md \
  --output-dir reports/ue-agent-comparison \
  --repeats 2
```

该受控 API Agent 测量整个发现与使用干预，不测量原生 Codex/Claude 技能集成，也不能分离已加载指令正文的独立效果。

仅把任务 prompt 展示给模型，隐藏 ground-truth 事实、预期路径、源码范围、应拒答的条件和工具使用要求。评测前冻结标签与技能，保留模型 digest、Ollama 版本、语料 revision/generation、prompt 和工具 schema 哈希、API 选项及执行顺序。每个任务对在重复间交替 AB/BA，计时前预热模型，串行推理以避免 GPU 争用。禁用输入截断和 context shifting；溢出请求必须失败，不能静默丢失证据。正式失败用例不重试，也不从分母中剔除。

Runner 记录原始模型/工具轨迹及 API 报告的 prompt/generated token。这些是本地 API 使用计数，不代表计费 token；缺失值保持缺失。最终引用复核在 Agent 计时外单独计时，检查 URI 是否由工具提供，再往返 `codalith_read` 比较 revision、generation、范围、当前/索引哈希与 stale。与 ground-truth 范围重叠表示证据覆盖，不代表回答事实正确。

按冻结 rubric，分别评分事实正确性、完整性、不支持的断言及恰当拒答，对审阅者隐藏组别和成本。报告普通任务上的源码查询和技能误触发，以及成对任务结果与成本。重复确定性运行测量稳定性；12 个任务仍是 12 个独立任务。这一小型实验不证明跨模型或 Agent host 的普遍优势，检索 benchmark 是另一个独立测量。

默认 profile 关闭原生 reasoning，每轮允许 1,536 个 generated token。独立补充 profile 可使用 `--think --output-tokens 3072`，但须使用自己的输出目录并冻结协议。Reasoning token 包含于 generated-token 预算和 API 计数。每个 profile 两组除技能发现外保持一致，并分别报告；同时改变 reasoning 和输出预算无法分离 reasoning 的独立作用。保留失败的主要实验结果，不能用补充运行替换。

## 原生 Codex 对比

`native-agent-benchmark` 为每次尝试在全新、物理隔离的 Docker 容器内运行官方 Codex CLI。两组使用相同原生模型、effort、developer 指令、最终 JSON schema 和七个 MCP 工具。仅技能组将原样候选挂载到 `.agents/skills`；原生发现最初仅暴露元数据，shell 事件显示是否真正读取正文。

```bash
uv run codalith native-agent-benchmark \
  --image sha256:<verified-native-cli-image> \
  --mcp-container codalith-mcp-http-ue-1 \
  --model gpt-6.1-sol --reasoning-effort ultra \
  --container-sandbox danger-full-access \
  --dataset benchmarks/datasets/ue-agent-tasks.jsonl \
  --skill skills/codalith-source/SKILL.md \
  --output-dir reports/ue-agent-native --repeats 2
```

上面的 image digest 是必须替换的占位符，不能直接运行。容器 root 与 workspace 挂载为只读，临时运行目录是易失的；已有认证文件只读挂载，供 Codex 自身的认证读取器使用。源码、索引、qrels、报告、宿主机历史和 Docker socket 均不挂载。通过共享 MCP 容器网络命名空间保留原回环 endpoint。保留 Docker 默认 capabilities 与 seccomp。

显式的内层 `danger-full-access` 依赖外部容器边界；Runner 默认 `read-only`，不会自动回退。Approval 固定为 `never`，禁用 web、apps、plugins、memories、delegation、ancestor instructions 与 bundled skills。两组均保留 desktop native originator 以进行账号路由。

正式回答前冻结数据集、技能、源码 generation、不可变镜像、请求模型、effort、配置、schema 和 ABBA/BAAB 顺序。原生尝试进程期限为 600 秒，无编排重试，也无人工 MCP/model 调用上限；与早期本地 API profile 不同，应直接比较两个原生组。计划分母保留失败和未运行用例。

原生 JSONL 原样保留。按 native item ID 对 MCP 和 shell 项去重，MCP 计数表示逻辑调用而非底层 HTTP 请求；native outer turns 不等于模型请求数。分别报告 input、cached input、output 和 reasoning output，不可得值保留 null。Cached input 是 input 子集，reasoning output 是 output 子集，总 token 为 input 加 output。耗时从进程启动到退出，包含容器启动、技能读取和 MCP；引用审计另计。服务端缓存与内部重试可能影响耗时，无法完全控制。

应用相同的盲审事实 rubric 和引用往返检查，独立检查所有原生 shell 命令是否绕过证据或认证边界，并将策略合规与事实成功同时纳入判定。需人工审阅的命令本身不等于违规；部分读取技能和无害的 workspace 元数据读取仍可有效。主要对比使用所有尝试，8 个源码任务另行报告。仅对已激活技能的条件结果属于描述性分析。

## 原生 Claude Code 对比

`claude-agent-benchmark` 使用已安装 Claude Code 版本的官方 CLI package、用户已有 gateway 配置和显式模型。隔离容器运行真实 Claude Agent loop。Runtime loader 通过只读挂载读取现有 settings，仅向 Claude 提供 provider 环境变量，不记录或复制凭证；两组应用相同控制，不改变全局设置。

```bash
uv run codalith claude-agent-benchmark \
  --image sha256:<verified-claude-cli-image> \
  --mcp-container codalith-mcp-http-ue-1 \
  --model 'glm-5.3[1M]' --effort max \
  --dataset benchmarks/datasets/ue-agent-tasks.jsonl \
  --skill skills/codalith-source/SKILL.md \
  --output-dir reports/ue-agent-claude --repeats 2
```

同样先替换并验证 image digest。两组具有相同的 Read/Skill built-in 和七个 MCP 工具。共同权限策略禁止读取隔离 workspace 以外的文件，并显式拒绝 runtime 配置与进程文件。A 没有候选技能；B 将原样字节挂载到 `.claude/skills`。保留正常原生发现，因为 bare/safe mode 会抑制元数据干预。

禁用 bundled skills、hooks、plugins、memory、delegation 和 tool search；检查 native initialization 事件，验证实际模型、工具和技能。仅冻结的 prompt 输入 stdin，共享指令和最终 JSON schema 保持一致。

保留原始 stream-json 事件，包括失败、拒绝权限、重试、tool-use ID、structured output 和最终累计 usage。Claude 的 `input_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens` 是不重叠的，总 input 为三者之和，再加 output 得总 token。不要将中间 assistant usage 加入最终累计计数；缺失字段保持 unknown，并单独展示不完整观察。客户端估算的美元成本不证明 GLM gateway 实际费用。

使用相同的 48 次尝试计划、600 秒期限、盲审 rubric、引用往返、原生工具合规检查和全尝试分母。将逻辑 MCP 调用与 Skill、Read、输出格式工具和重试分开报告。耗时包含 CLI/容器启动和工具工作，引用审计另计。请求及 CLI 报告的模型名标识路由，不能独立证明 gateway 背后的模型权重。

评测的英文源码协议与后续更新可在 [docs/evaluation.md](https://github.com/cyber-tao/Codalith/blob/master/docs/evaluation.md) 中核对。
