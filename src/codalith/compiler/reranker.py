"""Simple score normalization and ranking."""

from __future__ import annotations

from codalith.coderag.adapter import RetrievalHit


def rerank(
    hits: list[RetrievalHit],
    *,
    identifiers: list[str],
    max_hits: int,
    mode: str = "explain",
) -> list[RetrievalHit]:
    identifier_set = {item.lower() for item in identifiers}
    weights = _weights(mode)

    def score(hit: RetrievalHit) -> float:
        haystack = f"{hit.path}\n{hit.title}\n{hit.snippet}".lower()
        exact = 1.0 if any(identifier in haystack for identifier in identifier_set) else 0.0
        card = 1.0 if "UE_KNOWLEDGE" in hit.path else 0.0
        module = 1.0 if hit.module and hit.module.lower() in haystack else 0.0
        source_prior = 1.0 if hit.source == "ue-source-locator" else 0.0
        path_match = 1.0 if any(identifier in hit.path.lower() for identifier in identifier_set) else 0.0
        return (
            hit.score * weights["base"]
            + exact * weights["exact"]
            + card * weights["card"]
            + module * weights["module"]
            + source_prior * weights["source_prior"]
            + path_match * weights["path"]
        )

    ordered = sorted(hits, key=score, reverse=True)
    return ordered[:max_hits]


def _weights(mode: str) -> dict[str, float]:
    base = {
        "base": 1.0,
        "exact": 4.0,
        "card": 2.0,
        "module": 1.0,
        "source_prior": 8.0,
        "path": 2.0,
    }
    if mode == "api_usage":
        base.update({"exact": 6.0, "path": 3.0, "card": 1.0})
    elif mode == "debug":
        base.update({"source_prior": 10.0, "exact": 5.0})
    elif mode == "compare":
        base.update({"card": 0.5, "module": 3.0})
    elif mode == "implement":
        base.update({"card": 1.0, "module": 2.0})
    elif mode == "trace":
        # Call-path questions want exact source locations over knowledge cards.
        base.update({"source_prior": 9.0, "exact": 5.0, "path": 3.0, "card": 1.0})
    return base
