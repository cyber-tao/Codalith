# Configuration

Codalith uses two TOML files for static configuration. `${VAR}` is required; `${VAR:-default}` supplies a default. Relative paths resolve from the TOML file that declares them, never from the process working directory. Managed repositories, databases, build plans and tasks are stored in a durable SQLite catalog; published Git versions are registered from the shared pool, not written back into TOML.

## Corpus registry

`configs/registry.toml` has `schema_version`, `default_target`, directory-based `[[corpora]]`, optional `[[workspaces]]`, and an optional `[management]` section. A registry with `management.enabled = true` may start with no corpora and an empty default target so the dashboard can create its first database.

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

`adapter` is one of the IDs in `codalith.languages.catalog`: `python`, `csharp`, `cpp-ue`, or `generic`. `embedding_provider` is `fake`, `fastembed`, or `openai`. `semantic_store` is used only by `--semantic adopt`; `--semantic build` always embeds into the new generation. An adopted store must record the same embedding model and dimension as the configured provider, and once adopted its absolute path is pinned in the generation manifest, so later edits to `semantic_store` affect only future builds.

`include_extensions` uses suffix matching, so `.Build.cs` and `.Target.cs` are supported. An empty list means the adapter decides which files it supports.

`exclude_globs` matches canonical corpus-relative paths case-insensitively. A pattern containing `/` is anchored at the corpus root unless it starts with `**/`, so `Templates/**` excludes only the root `Templates` directory while `**/ThirdParty/**` excludes it at any depth. A pattern without `/` (such as `node_modules`) matches any single path segment at any depth.

## Shared Git databases and management

This feature is available in the current source checkout. Keep existing `[[corpora]]` entries when continuing to serve directory-based generations; management profiles can also be declared without creating a directory corpus:

```toml
schema_version = 2
default_target = ""

[management]
enabled = true
storage_root = "../data/management"
source_roots = ["/srv/sources"]
admin_token_env = "CODALITH_ADMIN_TOKEN"
max_selected_tags = 100

[[management.profiles]]
id = "ue-shared"
label = "Unreal Engine source"
adapter = "cpp-ue"
embedding_provider = "openai"
embedding_model = "text-embedding-3-small"
include_extensions = [".h", ".hpp", ".inl", ".cpp", ".c", ".cs", ".usf", ".ush"]
exclude_globs = ["**/ThirdParty/**", "**/Intermediate/**", "**/Binaries/**"]
```

Replace `/srv/sources` with an existing server-side source root; Windows TOML paths may use forward slashes, such as `D:/Sources`. Only directories beneath these roots are browsable or registerable. Mount source repositories read-only and the management storage root writable when using containers. A browser directory picker never uploads the client computer's source tree.

Before starting the service, set the environment variable named by `admin_token_env` to a private administrator token of **at least 16 characters**. The default name is `CODALITH_ADMIN_TOKEN`. Do not put its value in TOML, URLs, project bindings or MCP client configuration. Enter it in the dashboard connection form; the browser keeps it only in page memory, and reloading clears it. All `/api/admin/*` endpoints require this bearer token. Management is disabled by default; enabling it requires both a token and at least one source root.

A profile fixes the adapter, extensions, exclusions and embedding provider/model for a database. Existing static corpora are also offered as profiles. New profile IDs must be unique across both sources. Unlike directory corpora, explicit management profiles require a nonempty `include_extensions` list. Source-policy deny rules and byte limits still apply in addition to profile selection.

OpenAI-compatible profiles use `CODALITH_EMBEDDING_BASE_URL` and `CODALITH_EMBEDDING_API_KEY` from the service/worker process environment. Profiles store no credential values. Select a model compatible with imported legacy vectors; a shared semantic namespace separates differing provider/model/dimension/input configurations.

Management environment overrides are:

| Variable | Meaning |
| --- | --- |
| `CODALITH_MANAGEMENT_ENABLED` | `true`/`false` or `1`/`0`; overrides `enabled`. |
| `CODALITH_MANAGEMENT_STORAGE_ROOT` | Overrides the shared storage root. |
| `CODALITH_MANAGEMENT_SOURCE_ROOTS` | Overrides allowed source roots, separated by the operating system's path separator (`;` on Windows, `:` on Linux/macOS). |
| `CODALITH_ADMIN_TOKEN` | Default secret variable; use `admin_token_env` to select a different name. |

Use `enabled = true` in TOML, rather than an environment override alone, when starting with an empty corpus registry. See [Operations](operations.md#shared-database-workflow) for the dashboard and CLI workflow.

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

Serving and indexing do not mutate `.env` or client configuration. The explicit agent installer and `project bind` update only the selected MCP configurations and project binding, with backups; see [Installation](installation.md) and [Operations](operations.md#project-version-binding). Docker Compose loads `.env`; direct local commands require the relevant variables in the process environment.

Never commit `.env`, mounted source, generated indexes, reports, or credentials.
