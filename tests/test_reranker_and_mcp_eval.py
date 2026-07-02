from __future__ import annotations

import json
import threading

from codalith.coderag.adapter import RetrievalHit
from codalith.compiler.reranker import rerank
from codalith.eval.mcp_runner import run_mcp_eval
from codalith.gateway.http_server import StreamableHTTPConfig, create_http_server


class _FakeModelReranker:
    max_candidates = 10

    def rerank(self, query: str, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        assert query == "Where is Actor.h?"
        scored = []
        for hit in hits:
            score = 0.9 if hit.path.endswith("Actor.h") else 0.1
            scored.append(
                RetrievalHit(
                    **{
                        **hit.as_dict(),
                        "score": score,
                        "metadata": {**hit.metadata, "reranker_score": score},
                    }
                )
            )
        return sorted(scored, key=lambda hit: hit.score, reverse=True)


def test_rerank_can_use_model_reranker():
    hits = [
        _hit("Engine/Source/Runtime/Core/Public/Containers/Array.h", 0.8),
        _hit("Engine/Source/Runtime/Engine/Classes/GameFramework/Actor.h", 0.2),
    ]

    ordered = rerank(
        hits,
        identifiers=[],
        max_hits=2,
        query="Where is Actor.h?",
        model_reranker=_FakeModelReranker(),
    )

    assert ordered[0].path.endswith("Actor.h")
    assert ordered[0].metadata["reranker_score"] == 0.9


def test_mcp_eval_runner_calls_streamable_http(tools, tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "case-1",
                "query": "UPROPERTY ReplicatedUsing OnRep",
                "expected_files": ["Actor.h"],
                "expected_modules": ["Engine"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    server = create_http_server(tools, StreamableHTTPConfig(port=0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        report = run_mcp_eval(
            endpoint=f"http://{host}:{port}/mcp",
            dataset_path=dataset,
            label="test",
            max_source_spans=8,
            metric_k=5,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert report.count == 1
    assert report.file_recall_at_k == 1.0
    assert report.rows[0]["failure_class"] == "pass"


def _hit(path: str, score: float) -> RetrievalHit:
    return RetrievalHit(
        source="test",
        corpus_id="ue-5.7.4",
        uri=f"ue://5.7.4/source/{path}#L1-L3",
        path=path,
        start_line=1,
        end_line=3,
        title=path,
        snippet=path,
        score=score,
        module="Engine" if "Engine" in path else "Core",
    )
