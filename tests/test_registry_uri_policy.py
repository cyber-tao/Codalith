from __future__ import annotations

import pytest

from ue_context.corpus.source_policy import SourcePolicy
from ue_context.corpus.uri_resolver import URIResolver
from ue_context.errors import SourcePolicyError, URIResolutionError


def test_registry_resolves_engine_and_project(registry):
    assert registry.get_engine("5.7.4").corpus_id == "ue-5.7.4"
    resolution = registry.resolve("5.7.4", "ProjectA", True)
    assert resolution.engine.corpus_id == "ue-5.7.4"
    assert resolution.project is not None
    assert resolution.project.corpus_id == "ProjectA"


def test_uri_resolver_parses_source_uri(registry):
    resolved = URIResolver(registry).resolve_source(
        "ue://5.7.4/source/Engine/Source/Runtime/Core/Public/CoreMinimal.h#L2-L4"
    )
    assert resolved.corpus_id == "ue-5.7.4"
    assert resolved.relative_path.endswith("CoreMinimal.h")
    assert resolved.start_line == 2
    assert resolved.end_line == 4


def test_uri_resolver_rejects_bad_scheme(registry):
    with pytest.raises(URIResolutionError):
        URIResolver(registry).resolve_source("file:///etc/passwd")


def test_source_policy_enforces_limits_and_scope(registry, policy_path):
    resolver = URIResolver(registry)
    policy = SourcePolicy.from_file(str(policy_path))
    ok = resolver.resolve_source(
        "ue://5.7.4/source/Engine/Source/Runtime/Core/Public/CoreMinimal.h#L1-L5"
    )
    policy.check(ok, {"source:read"})
    too_large = resolver.resolve_source(
        "ue://5.7.4/source/Engine/Source/Runtime/Core/Public/CoreMinimal.h#L1-L21"
    )
    with pytest.raises(SourcePolicyError):
        policy.check(too_large, {"source:read"})
