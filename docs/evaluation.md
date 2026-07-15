# Evaluation

Benchmarks always use an actual MCP Streamable HTTP session. They do not call `QueryService` directly and do not accept fallback output hidden behind a successful process exit.

## Datasets

- `sample-smoke.jsonl`: deterministic English/Chinese/code/text/symbol/negative smoke cases.
- `ue-regression.jsonl`: migrated 80-case historical UE suite.
- `ue-holdout.jsonl`: independently selected UE questions used as the tuning guard.

Each JSONL row is strict and contains `id`, `query`, optional `target`, `strategy`, expected files/symbols, negative flag, language, and category. Legacy or unknown fields are rejected.

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

Ranked paths and symbols are deduplicated before scoring. Every top-five citation is round-tripped through `codalith_read` and must match its URI and current source hash.

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
```

Run regression and holdout separately. Do not edit holdout labels in response to retrieval failures; diagnose adapter/index/query behavior first, then add genuinely new cases without replacing failed ones.
