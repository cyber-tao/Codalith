# Evaluation

Benchmarks always use an actual MCP Streamable HTTP session. They do not call `QueryService` directly and do not accept fallback output hidden behind a successful process exit.

## Datasets

- `sample-smoke.jsonl`: deterministic English/Chinese/code/text/symbol/negative smoke cases.
- `ue-regression.jsonl`: migrated 80-case historical UE suite (78 `auto`, 2 `symbol`).
- `ue-holdout.jsonl`: 40 independently selected UE questions and the primary quality signal.

Each JSONL row is strict and contains `id`, `query`, optional `target`, `strategy`, expected files/symbols, negative flag, language, and category. Legacy or unknown fields are rejected.

The UE sets contain no negative cases and a single `text` case, so the negative-pass gate and literal search are only exercised by the sample set. Earlier UE regression scores were inflated by query rules that encoded this suite's answers; those rules are gone, so compare new runs against a fresh baseline rather than historical reports.

## Metrics and default gates

- file recall@5 ≥ 0.85
- symbol recall@5 ≥ 0.80
- MRR ≥ 0.65
- nDCG@10 ≥ 0.75
- citation validity = 1.00
- degraded rate = 0
- negative pass rate = 1.00 when negatives exist
- p95 latency ≤ 2,000 ms
- errors = 0

Ranked paths and symbols are deduplicated before scoring. Every top-five citation is round-tripped through `codalith_read` and must match its URI and generation, and must not be stale; run benchmarks against a stable generation.

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

Retrieval code must never encode evaluation questions, keywords, or expected answer paths. Language and engine conventions reach the query layer only through an adapter's `RetrievalProfile` (`codalith.languages.catalog`), and every ranking change must be justified by behavior on unseen questions. Treat the regression set as a guard and the holdout set as the quality signal.
