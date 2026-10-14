---
name: codalith-source
description: Use Codalith MCP to retrieve and cite versioned, indexed source for implementation questions, symbol discovery, and code relationships. Apply when source evidence is needed from an available corpus; skip ordinary tasks that do not need source inspection.
---

# Codalith source evidence

Use the existing Codalith MCP tools. Select the indexed corpus or workspace matching the requested project and version, and pass `target` explicitly where supported. Available corpora are described by the server. Use `codalith_status` when readiness, revision, or target selection is uncertain; reuse that information while it remains applicable.

Choose the tool that supplies the missing evidence; these are alternatives, not a mandatory sequence:

- **Implementation or behavior questions:** Prefer `codalith_context`, which retrieves source and compiles a bounded context pack in one call. An initial request often needs only 3-5 spans and 6,000-10,000 characters, such as `max_spans=4, max_chars=8000`. Adjust to the question and returned truncation rather than treating these as fixed limits.
- **Known identifiers:** Use `codalith_symbol` for definitions and canonical symbol URIs. Start with an exact name; use fuzzy lookup or broader discovery when needed.
- **Discovery:** Use `codalith_search` with `auto` for mixed discovery or `semantic` for conceptual descriptions. Use `text` for the complete literal string being sought, not a natural-language question. Request a small result set first.
- **Additional source:** Use `codalith_read` for an exact range that is missing, abbreviated, or truncated. Reuse returned canonical source URIs with `#Lx-Ly` ranges. Fresh, matching-hash context spans already provide verified source; do not read the same span again merely to verify it.
- **Relationships:** Use `codalith_graph` with a returned symbol URI when callers, callees, or related symbols matter. Start with the relevant direction and a shallow depth; expand only a useful branch.
- **Version differences:** Use `codalith_compare` only when both requested versions have indexed corpora. Its structural comparison does not establish runtime equivalence.

Ground conclusions in the returned source text. Cite the relevant canonical URI and range, and retain revision and generation information when identifying the evidence. Distinguish an observed declaration or implementation from an inferred runtime effect. Stop retrieving once the evidence answers the question; fetch only the specific missing part when it does not.

Inspect `degraded`, `warnings`, `stale`, decoding state, and `truncated` before relying on a result. Explain material coverage gaps. Do not present stale source or results from different generations as one verified snapshot. Each call is generation-consistent; separate calls may observe a newly published generation.

An empty symbol lookup does not prove absence: macros, generated declarations, or partial parsing can limit structural extraction. A graph's `name_match` is name-based evidence; `ambiguous` and `unresolved` edges do not establish resolved calls. Truncated results do not establish exhaustive coverage.

For unavailable or invalid indexes, report the affected target and limitation. Use retryable errors to decide whether a focused retry is useful. Querying does not authorize building or rebuilding indexes, changing configuration, or opening database files directly. Do not run deep doctor routinely; it is an explicit diagnostic operation, not a prerequisite for source questions.
