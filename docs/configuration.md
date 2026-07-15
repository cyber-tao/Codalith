# Configuration

Codalith uses two TOML files. `${VAR}` is required; `${VAR:-default}` supplies a default. Relative paths resolve from the TOML file that declares them, never from the process working directory.

## Corpus registry

`configs/registry.toml` has `schema_version`, `default_target`, one or more `[[corpora]]`, and optional `[[workspaces]]`.

```toml
schema_version = 2
default_target = "${CODALITH_DEFAULT_TARGET:-sample}"

[[corpora]]
id = "sample"
display_name = "Sample"
description = "Small local corpus"
revision = "sample-v1"
source_root = "../fixtures/sample_corpus"
index_root = "${CODALITH_SAMPLE_INDEX_ROOT:-../data/indexes/sample}"
adapter = "python"
embedding_provider = "fake"
include_extensions = [".py"]
exclude_globs = [".git/**", ".venv/**", "**/__pycache__/**"]

[[workspaces]]
id = "all"
corpora = ["sample"]
```

`adapter` is `python`, `csharp`, `cpp-ue`, or `generic`. `embedding_provider` is `fake`, `fastembed`, or `openai`. `coderag_store` is optional; when absent, semantic data belongs to the generation. When present, `adopt` uses an external store and records a full fingerprint.

`include_extensions` uses suffix matching, so `.Build.cs` and `.Target.cs` are supported. An empty list means the adapter decides which files it supports.

`exclude_globs` matches canonical corpus-relative paths case-insensitively. Use explicit root patterns such as `Templates/**` separately from recursive patterns such as `**/ThirdParty/**`; directory basenames are never implicitly excluded at every depth.

## Source policy

`configs/source-policy.toml` defines default/hard line budgets, maximum source bytes, and at least one deny glob.

Patterns are checked case-insensitively at indexing and read time. `**/.env` matches both root and nested files. Traversal, absolute paths, empty segments, and dot segments are rejected before glob matching.

## Environment

`.env.example` contains Compose-facing settings. Important groups are:

- `CODALITH_HTTP_*`: bind address, port, endpoint, allowed Host/Origin additions, body limit.
- `CODALITH_EMBEDDING_*`: endpoint and key required by OpenAI-compatible corpora.
- `LANCE_INCLUDE_VECTOR_CENTROIDS=false`: avoids loading large IVF centroid statistics.
- `CODALITH_UE_*_HOST_DIR`: host bind mounts for UE source, structural indexes, and CodeRAG store.
- `CODALITH_UE_EMBEDDING_*`: provenance expected by the adopted/rebuilt UE store.

The application does not mutate `.env` or client configuration. Docker Compose loads `.env`; direct local commands require the relevant variables in the process environment.

Never commit `.env`, mounted source, generated indexes, reports, or credentials.
