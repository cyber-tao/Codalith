# Evaluation

Benchmarks always use an actual MCP Streamable HTTP session. They do not call `QueryService` directly and do not accept fallback output hidden behind a successful process exit.

## Datasets

- `sample-smoke.jsonl`: deterministic English/Chinese/code/text/symbol/negative smoke cases.
- `ue-regression.jsonl`: migrated 80-case historical UE suite (78 `auto`, 2 `symbol`).
- `ue-holdout.jsonl`: 40 independently selected UE questions and the primary quality signal.
- `ue-literal.jsonl`: a unique-file logging macro definition and an absent literal, evaluated separately for file retrieval and citations.
- `ue-semantic.jsonl`: 13 English/Chinese questions without symbol or file names, using the semantic strategy across audio, animation, physics, HTTP, sockets, navigation, camera, and rendering. It evaluates file retrieval and citations only.

Each JSONL row is strict and contains `id`, `query`, optional `target`, `strategy`, expected files/symbols, negative flag, language, and category. Legacy or unknown fields are rejected.

The regression and holdout sets contain no negative cases and no `semantic`-only or `text` cases. Use the separate diagnostic sets to exercise these strategies. Semantic search combines dense embeddings and BM25, so its scores measure the semantic retrieval pipeline rather than embeddings in isolation. Earlier UE regression scores were inflated by query rules that encoded this suite's answers; those rules are gone, so compare new runs against a fresh baseline rather than historical reports.

Before the UE 5.7.4 rebuild, all 120 regression/holdout cases and their 95 distinct expected paths were checked against `5.7.4-release` (`260bb2e1c5610b31c63a36206eedd289409c5f11`). The holdout collision structure label was corrected from `EngineTypes.h` to `HitResult.h`, which declares the structure. The logging question retains its wording and uses `auto`; its former `text` strategy mixed a natural-language question with a literal-search contract. The separate literal diagnostic uses the complete `UE_LOG_ACTIVE` macro definition from `LogMacros.h:255`, verified by case-insensitive fixed-string search over the full pinned snapshot to occur only in that file. The broad `UE_LOG` token has legitimate call sites across the corpus, and even its macro signature appears in a plugin, so a single-file relevance label would misjudge those valid matches. Literal text hits do not guarantee symbol metadata, so this diagnostic has no symbol ground truth; its absent-literal case is unchanged.

Four regression symbol labels were corrected against source declarations before evaluation. `Renderer.cpp:79` implements `FRendererModule::StartupModule`, rather than declaring the renderer class. `DelegateSignatureImpl.inl:1222` implements `__Internal_AddDynamic`; the public macro and reflection macro were only mentioned or invoked in the expected files. `TimerManager.h:276` declares `ClearTimer`, while the timer handle is only a parameter type in that file. `Actor.h:273` declares `GetLifetimeReplicatedProps`, while reflection-property macros are invocations; the replication macro remains defined in `UnrealNetwork.h:259`. Questions and expected files are unchanged for these four cases. These corrections precede retrieval evaluation and do not respond to benchmark failures. Public aggregation headers and macro-generated types remain valid source labels; extraction failures must be diagnosed in the adapter rather than relabeled to improve scores.

## Metrics and default gates

- file recall@5 ≥ 0.85
- symbol recall@5 ≥ 0.80 when symbol ground truth exists
- MRR ≥ 0.65
- nDCG@10 ≥ 0.75
- citation validity = 1.00
- degraded rate = 0
- negative pass rate = 1.00 when negatives exist
- p95 latency ≤ 2,000 ms
- errors = 0

Ranked paths are deduplicated before scoring; symbols are ranked as distinct `(path, symbol)` pairs. Symbol matches are case-sensitive, allow a qualified-name suffix, and must belong to an expected file when expected files are specified. Failed queries contribute zero to every applicable quality metric. Metrics without corresponding ground truth are `null` (N/A) and do not apply a quality gate. This applies to file-only, symbol-only, and negative-only datasets; an incomplete report, an unscored positive row, or a missing aggregate whose rows contain applicable scores remains an error. Citation, degraded, latency, and error gates always apply.

The entire search response, including every returned hit, must satisfy the public response schema before scoring. Every top-five citation is round-tripped through `codalith_read` and must match its URI, corpus, revision, generation, path, and line range, with matching current/indexed SHA-256 hashes and no stale flag; run benchmarks against a stable generation. Latency measures the search call and schema validation, excluding successful citation verification; separately measure cold-start and repeated warm runs when comparing deployment performance.

## Commands

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

Run regression and holdout separately. Do not edit holdout labels in response to retrieval failures; diagnose adapter/index/query behavior first, then add genuinely new cases without replacing failed ones.

Run `ue-literal.jsonl` and `ue-semantic.jsonl` separately with the same command pattern and distinct output paths. The semantic diagnostic domains were excluded from model-selection probes. C++ semantic chunking currently uses line windows with no symbol metadata, and `strategy=semantic` does not run structural symbol retrieval. Therefore these diagnostic cases retain their source-grounded file labels and have no symbol ground truth; symbol recall is N/A. This dataset design follows the pipeline contract before any UE retrieval run. A small diagnostic set does not replace the holdout set or establish a general model-quality ranking. Save the source commit, generation manifest, model identity/dimension, dataset hashes, and cold/warm run conditions alongside local reports.

Retrieval code must never encode evaluation questions, keywords, or expected answer paths. Language and engine conventions reach the query layer only through an adapter's `RetrievalProfile` (`codalith.languages.catalog`), and every ranking change must be justified by behavior on unseen questions. Treat the regression set as a guard and the holdout set as the quality signal.

## Agent workflow comparison

`agent-benchmark` compares an MCP-only agent with an agent that can discover and read
`skills/codalith-source/SKILL.md` on demand. Both use the same local Ollama model,
server instructions, seven advertised MCP tools, independent conversation histories,
output contract, and MCP call budget. The skill arm additionally receives its name,
description, and a `read_skill` tool; loading the body costs a model turn and contributes
to the recorded token and wall-clock totals. This controlled API agent measures the
complete discovery-and-use intervention, not native Codex or Claude skill integration
or the isolated effect of an already-loaded instruction body.

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

The task prompts alone are shown to the model. Ground-truth facts, expected paths,
source ranges, abstention requirements, and tool-use expectations remain hidden.
Freeze these labels and the skill before evaluating. Preserve the model digest,
Ollama version, corpus revision and generation, prompt and tool-schema hashes, API
options, and execution order. Alternate AB/BA within task pairs across repetitions,
warm the model before timing, and serialize inference to avoid GPU contention.
Disable input truncation and context shifting; overflowing requests must fail rather
than silently lose evidence. Do not retry failed formal cases or remove them from the
denominator.

The runner records raw model/tool traces and API-reported prompt and generated token
counts. These are local API usage counters, not a claim about billable tokens. Missing
usage remains missing. Final citation verification is timed separately from the agent:
check that a URI was supplied by a tool, round-trip it through `codalith_read`, and
compare revision, generation, range, current/indexed hashes, and stale state.
Ground-truth range overlap is evidence coverage, not proof that an answer is correct.

Grade factual correctness, answer completeness, unsupported claims, and appropriate
abstention separately against the frozen rubric, with arm labels and cost metrics
hidden from the reviewer. Report source-query and skill false activations on ordinary
tasks, as well as task-level paired outcomes and costs. Repeated deterministic runs
measure stability; twelve tasks remain twelve independent tasks. This small experiment
does not establish a general advantage across models or agent hosts, and the existing
retrieval benchmarks remain a separate measurement.

The default profile disables native reasoning and allows 1,536 generated tokens per
turn. An independent supplementary profile can use `--think --output-tokens 3072`
with its own output directory and frozen protocol. Reasoning tokens are included in
the API's generated-token budget and counters. Keep each profile's two arms identical
apart from skill discovery, and report profiles separately: changing both reasoning
and output budget cannot isolate the effect of reasoning alone. Preserve failed
primary results rather than replacing them with the supplementary run.

## Native Codex comparison

`native-agent-benchmark` runs the official Codex CLI in a fresh, physically isolated
Docker container for each attempt. Both arms receive the same native model, effort,
developer instructions, final JSON schema, and seven MCP tools. Only the skill arm
mounts the unchanged candidate under `.agents/skills`; native discovery initially
exposes metadata, and shell events show whether the body was actually read.

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

The container root and workspace mounts are read-only, temporary runtime directories
are ephemeral, and the existing authentication file is mounted read-only for Codex's
own authentication reader. Source, index, qrels, reports, host history, and Docker
socket are absent. Sharing the MCP container's network namespace preserves the
original loopback endpoint. Preserve Docker's default capabilities and seccomp. The
explicit inner `danger-full-access` profile relies on this external container
boundary; the runner defaults to `read-only` and never falls back automatically.
Approval is fixed to `never`; web, apps, plugins, memories, delegation, ancestor
instructions, and bundled skills are disabled. Both arms retain the desktop native
originator for account routing.

Freeze the dataset, skill, source generation, immutable image, requested model,
effort, configuration, schema, and ABBA/BAAB schedule before formal answers. Native
attempts have a 600-second process deadline and no orchestrator retries or artificial
MCP/model-call cap. This differs from the earlier local API profile; compare the two
native arms directly. Preserve failures and unrun cases in the planned denominator.

Native JSONL is retained verbatim. Deduplicate MCP and shell items by their native
item IDs; MCP counts describe logical calls, not underlying HTTP requests. Native
outer turns are not model-request counts. Report input, cached input, output, and
reasoning output counters separately, leaving unavailable fields null. Cached input
is part of input and reasoning output is part of output: total tokens are input plus
output. Wall time covers process startup through exit, including container startup,
skill reads, and MCP work; citation auditing is measured separately. Provider-side
caching and internal retries may affect elapsed time and cannot be fully controlled.

Apply the unchanged blinded factual rubric and citation round-trip checks. Review all
native shell commands independently for evidence or authentication bypass, and AND
that policy assessment with factual success. A command needing review is not itself
a violation; partial skill reads and harmless workspace metadata reads remain valid.
Use all attempts for the primary comparison, with the eight source tasks reported
separately. Conditional results for activated skills are descriptive only.

## Native Claude Code comparison

`claude-agent-benchmark` uses the installed Claude Code version's official CLI
package, with the user's existing gateway configuration and an explicitly selected
model. Isolated containers run the real Claude agent loop. The runtime loader reads
the existing settings through a read-only mount, supplies provider environment
variables to Claude without logging or copying credentials, and applies common
controls for both arms. No global settings are changed.

```bash
uv run codalith claude-agent-benchmark \
  --image sha256:<verified-claude-cli-image> \
  --mcp-container codalith-mcp-http-ue-1 \
  --model 'glm-5.3[1M]' --effort max \
  --dataset benchmarks/datasets/ue-agent-tasks.jsonl \
  --skill skills/codalith-source/SKILL.md \
  --output-dir reports/ue-agent-claude --repeats 2
```

Both arms expose the same Read and Skill built-ins and the same seven MCP tools.
The common permission policy blocks file reads outside the isolated workspace and
explicitly denies runtime configuration and process files. A has no candidate;
B mounts its unchanged bytes under `.claude/skills`. Preserve normal native skill
discovery: bare and safe modes would suppress the metadata intervention. Disable
bundled skills, hooks, plugins, memory, delegation, and tool search; inspect native
initialization events to verify the effective model, tools, and skills. The frozen
prompts alone enter stdin. Shared instructions and the final JSON schema are identical.

Retain raw stream-json events, including failures, permission denials, retries,
tool-use IDs, structured output, and the final cumulative usage. Claude input usage
differs from Codex: `input_tokens`, `cache_read_input_tokens`, and
`cache_creation_input_tokens` are disjoint, so total input is their sum; total tokens
add output once. Do not sum intermediate assistant usage into the final cumulative
result. Keep missing fields unknown and separately expose partial observations.
Client-estimated dollar costs do not establish actual GLM gateway charges.

Use the same 48-attempt schedule, 600-second deadline, blinded rubric, citation
round-trips, native tool compliance review, and all-attempt denominator. Report
logical MCP calls separately from Skill, Read, output-format tools, and retries.
Wall time includes CLI/container startup and tool work; citation audits are separate.
Requested and CLI-reported model names identify the route but cannot independently
prove the gateway's underlying model weights.
