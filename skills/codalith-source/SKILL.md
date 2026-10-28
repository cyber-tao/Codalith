---
name: codalith-source
description: Retrieve and cite versioned source through Codalith MCP for implementation questions, symbol discovery, and code relationships. Use when a task needs indexed source evidence; skip ordinary tasks without source inspection.
---

# Codalith source evidence

Use the existing Codalith MCP tools and pass the matching project/version `target` explicitly where supported. The server lists registered corpora and workspaces; registration does not establish index readiness. Use `codalith_status` only when readiness, revision, or target selection is uncertain, and reuse its result while applicable. If the requested version is unavailable, state the limitation instead of substituting another version's implementation.

Use `codalith_catalog` when the available version targets are unknown. A project can be detected with `codalith project detect` and explicitly bound with `codalith project bind`; the version-bound HTTP endpoint enforces the selected target even when a tool omits it. Recheck the binding after upgrading the engine. Cross-version comparisons require the ordinary MCP endpoint.

Choose the tool that supplies the missing evidence; these are alternatives, not a mandatory sequence:

- **Implementation:** Prefer `codalith_context` for retrieval plus source text. Start with a bounded pack, for example `max_spans=4, max_chars=8000`; adjust to missing evidence and truncation.
- **Known identifier:** Use `codalith_symbol` for definitions and their `source_uri`; exact lookup first.
- **Discovery:** Use `codalith_search` with `auto` or conceptual `semantic`. Use `text` for the complete literal string, not a question. Start with a small result set.
- **Missing source:** Use `codalith_read` when a snippet or span omits the relevant implementation. Fresh matching-hash context text already supplies verified evidence; do not mechanically reread it.
- **Relationships:** Use `codalith_graph` with a returned symbol URI. Expand a shallow, relevant branch only when it can answer a remaining question.
- **Versions:** Use `codalith_compare` when both versions are indexed; its structural differences do not establish runtime equivalence.

Ground each implementation claim in returned source text. Copy an issued `codalith://<corpus>/source/<path>#Lx-Ly` URI exactly when citing; `symbol/...`, status, and bare corpus URIs are identifiers, not source citations. To cite a narrower or wider range, request it with `codalith_read` first and use the response's actual URI. Do not invent or edit citation line ranges. Keep revision/generation provenance when identifying the snapshot.

Answer the requested facts first and omit optional implementation details unless the retrieved spans establish them. A valid URI does not prove its attached claim. Before asserting "only", "always", or "never", check whether unexamined paths or shared/enclosing state could change the result; otherwise qualify the conclusion.

Stop when the evidence answers the question. If static source cannot establish a runtime fact, explain that boundary rather than expanding searches indefinitely: a call site or delegate declaration does not enumerate callbacks registered in a running process, their registration history, or their execution order. Separate visible code order from runtime assumptions.

Check `degraded`, `warnings`, `stale`, decoding state, and `truncated`; explain material gaps. Each call is generation-consistent, but separate calls can observe a new generation. Do not merge stale or different-generation evidence into one verified snapshot.

An empty symbol lookup does not prove API absence: macros, generated declarations, or partial parsing can limit extraction. Use focused text/context evidence if absence matters, and qualify the checked scope. Graph `name_match` is name-based evidence; `ambiguous` or `unresolved` edges do not prove resolved calls. Truncated results are not exhaustive.

Report unavailable or invalid indexes and retry only when the error makes a focused retry useful. Source queries do not authorize rebuilding indexes, changing configuration, or opening databases directly. Deep doctor is an explicit diagnostic operation, not a routine prerequisite.
