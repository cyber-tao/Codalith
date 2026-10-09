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

`adapter` is one of the IDs in `codalith.languages.catalog`: `python`, `csharp`, `cpp-ue`, or `generic`. `embedding_provider` is `fake`, `fastembed`, or `openai`. `coderag_store` is used only by `--semantic adopt`; `--semantic build` always embeds into the new generation. An adopted store must record the same embedding model and dimension as the configured provider, and once adopted its absolute path is pinned in the generation manifest, so later edits to `coderag_store` affect only future builds.

`include_extensions` uses suffix matching, so `.Build.cs` and `.Target.cs` are supported. An empty list means the adapter decides which files it supports.

`exclude_globs` matches canonical corpus-relative paths case-insensitively. A pattern containing `/` is anchored at the corpus root unless it starts with `**/`, so `Templates/**` excludes only the root `Templates` directory while `**/ThirdParty/**` excludes it at any depth. A pattern without `/` (such as `node_modules`) matches any single path segment at any depth.

## Source policy

`configs/source-policy.toml` defines default/hard line budgets, maximum source bytes, and at least one deny glob.

Patterns follow the same matching rules and are checked case-insensitively at indexing, on every query output, and at read time. `**/.env` matches both root and nested files. Traversal, absolute paths, empty segments, and dot segments are rejected before glob matching. The deny globs and byte limit are part of each generation's source-selection hash: changing them invalidates existing generations until they are rebuilt, and denied paths are withheld from results in the meantime.

## Environment

`.env.example` contains Compose-facing settings. Important groups are:

- `CODALITH_HTTP_*`: bind address, port, endpoint, allowed Host/Origin additions, body limit.
- `CODALITH_EMBEDDING_*`: endpoint and key required by OpenAI-compatible corpora.
- `LANCE_INCLUDE_VECTOR_CENTROIDS=false`: avoids loading large IVF centroid statistics.
- `CODALITH_UE_*_HOST_DIR`: host bind mounts for UE source, structural indexes, and the semantic store.
- `CODALITH_UE_EMBEDDING_*`: provenance expected by the adopted UE store.

Compose sets the in-container registry, source policy, and listen host itself. `CODALITH_REGISTRY` and `CODALITH_SOURCE_POLICY` select the TOML files for direct local commands, and `CODALITH_DASHBOARD_DIR` points the HTTP server at prebuilt dashboard assets.

The application does not mutate `.env` or client configuration. Docker Compose loads `.env`; direct local commands require the relevant variables in the process environment.

Never commit `.env`, mounted source, generated indexes, reports, or credentials.
