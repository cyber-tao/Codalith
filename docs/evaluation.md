# Evaluation

Benchmarks always use an actual MCP Streamable HTTP session. They do not call `QueryService` directly and do not accept fallback output hidden behind a successful process exit.

## Datasets

- `sample-smoke.jsonl`: deterministic English/Chinese/code/text/symbol/negative smoke cases.
- `ue-regression.jsonl`: migrated 80-case historical UE suite (78 `auto`, 2 `symbol`).
- `ue-holdout.jsonl`: 40 independently selected UE questions and the primary quality signal.
- `ue-literal.jsonl`: an exact logging token and an absent literal, evaluated separately.
- `ue-semantic.jsonl`: 13 English/Chinese questions without symbol or file names, using the semantic strategy across audio, animation, physics, HTTP, sockets, navigation, camera, and rendering. It evaluates file retrieval and citations only.

Each JSONL row is strict and contains `id`, `query`, optional `target`, `strategy`, expected files/symbols, negative flag, language, and category. Legacy or unknown fields are rejected.

The regression and holdout sets contain no negative cases and no `semantic`-only or `text` cases. Use the separate diagnostic sets to exercise these strategies. Semantic search combines dense embeddings and BM25, so its scores measure the semantic retrieval pipeline rather than embeddings in isolation. Earlier UE regression scores were inflated by query rules that encoded this suite's answers; those rules are gone, so compare new runs against a fresh baseline rather than historical reports.

Before the UE 5.7.4 rebuild, all 120 regression/holdout cases and their 95 distinct expected paths were checked against `5.7.4-release` (`260bb2e1c5610b31c63a36206eedd289409c5f11`). The holdout collision structure label was corrected from `EngineTypes.h` to `HitResult.h`, which declares the structure. The logging question retains its wording and uses `auto`; its former `text` strategy mixed a natural-language question with a literal-search contract. The separate literal diagnostic uses the logging token itself.

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
