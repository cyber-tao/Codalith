"""Official MCP SDK stdio entry point for Codalith."""

from __future__ import annotations

import anyio
from mcp.server.stdio import stdio_server

from codalith.corpus.registry import CorpusRegistry
from codalith.gateway.sdk_server import create_sdk_server
from codalith.gateway.tools import CodalithTools, create_runtime


def build_instructions(registry: CorpusRegistry) -> str:
    """Assemble the server self-description from the configured corpus registry.

    Nothing domain-specific is hardcoded here: corpus names, descriptions, and
    trigger keywords all come from the configured corpus registry, so a deployment
    that indexes a different codebase advertises that codebase instead.
    """
    corpus_labels = [
        f"{corpus.label} ({corpus.description})" if corpus.description else corpus.label
        for corpus in registry.corpora.values()
    ]
    project_ids = sorted(registry.projects)
    keywords = list(
        dict.fromkeys(
            keyword for corpus in registry.corpora.values() for keyword in corpus.keywords
        )
    )
    parts = ["This server provides version-pinned, source-backed context for indexed code corpora."]
    if corpus_labels:
        parts.append("Indexed corpora: " + "; ".join(corpus_labels) + ".")
    if project_ids:
        parts.append("Project overlays: " + ", ".join(project_ids) + ".")
    parts.append(
        "Use this server first for any source-level question about these codebases, even when "
        "the question does not mention source code. Call codalith_context before answering "
        "implementation, API, behavior, or version-specific questions."
    )
    parts.append(
        "Source citations use codalith://<corpus>/source/<path>#L<start>-L<end> URIs; "
        "read them with codalith_read_source."
    )
    if keywords:
        parts.append("Trigger topics include: " + ", ".join(keywords) + ".")
    return " ".join(parts)


async def serve(tools: CodalithTools) -> None:
    server = create_sdk_server(
        tools,
        instructions=build_instructions(tools.runtime.registry),
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> int:
    runtime = create_runtime()
    anyio.run(serve, CodalithTools(runtime))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
