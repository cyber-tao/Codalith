from __future__ import annotations

import pytest

from codalith.corpus.source_policy import SourcePolicy, SourceReadRateLimiter
from codalith.corpus.uri_resolver import URIResolver
from codalith.errors import SourcePolicyError, URIResolutionError


def test_registry_resolves_base_and_project(registry):
    assert registry.get_base("sample").corpus_id == "sample-codebase"
    resolution = registry.resolve("sample", "SampleProject", True)
    assert resolution.base.corpus_id == "sample-codebase"
    assert resolution.project is not None
    assert resolution.project.corpus_id == "SampleProject"
    assert not resolution.overlays


def test_registry_resolves_generated_overlay_only_when_requested(registry):
    resolution = registry.resolve("sample", include_generated_overlay=True)

    assert [corpus.corpus_id for corpus in resolution.ordered] == [
        "generated-sample",
        "sample-codebase",
    ]


def test_uri_resolver_parses_source_uri(registry):
    resolved = URIResolver(registry).resolve_source(
        "codalith://sample-codebase/source/src/core/cache.py#L2-L4"
    )
    assert resolved.corpus_id == "sample-codebase"
    assert resolved.relative_path.endswith("cache.py")
    assert resolved.start_line == 2
    assert resolved.end_line == 4


def test_uri_resolver_parses_generated_source_uri(registry):
    resolved = URIResolver(registry).resolve_source(
        "codalith://generated-sample/source/generated/build.log#L1-L2"
    )

    assert resolved.corpus_id == "generated-sample"
    assert resolved.source_kind == "generated"
    assert resolved.relative_path == "generated/build.log"


def test_uri_resolver_accepts_version_alias_authority(registry):
    resolved = URIResolver(registry).resolve_source(
        "codalith://sample/source/src/core/cache.py#L2-L4"
    )
    assert resolved.corpus_id == "sample-codebase"


def test_uri_resolver_resolves_project_corpus(registry):
    resolved = URIResolver(registry).resolve_source(
        "codalith://SampleProject/source/src/project/feature.py#L1-L3"
    )
    assert resolved.corpus_id == "SampleProject"
    assert resolved.source_kind == "project"


def test_uri_resolver_rejects_bad_scheme(registry):
    with pytest.raises(URIResolutionError):
        URIResolver(registry).resolve_source("file:///etc/passwd")


def test_uri_resolver_rejects_unknown_corpus(registry):
    with pytest.raises(URIResolutionError):
        URIResolver(registry).resolve_source("codalith://nope/source/A.py#L1-L2")


def test_source_policy_enforces_limits_and_scope(registry, policy_path):
    resolver = URIResolver(registry)
    policy = SourcePolicy.from_file(str(policy_path))
    ok = resolver.resolve_source("codalith://sample-codebase/source/src/core/cache.py#L1-L5")
    policy.check(ok, {"source:read"})
    within_hard_max = resolver.resolve_source(
        "codalith://sample-codebase/source/src/core/cache.py#L1-L21"
    )
    policy.check(within_hard_max, {"source:read"})
    too_large = resolver.resolve_source("codalith://sample-codebase/source/src/core/cache.py#L1-L26")
    with pytest.raises(SourcePolicyError):
        policy.check(too_large, {"source:read"})


def test_source_read_rate_limiter_enforces_read_and_line_budgets():
    policy = SourcePolicy(
        default_max_lines=20,
        hard_max_lines=25,
        max_source_reads_per_10min=2,
        max_total_lines_per_10min=8,
    )
    limiter = SourceReadRateLimiter(policy, time_func=lambda: 100.0)

    limiter.record_read(line_count=3)
    limiter.record_read(line_count=4)
    with pytest.raises(SourcePolicyError):
        limiter.record_read(line_count=1)

    line_limiter = SourceReadRateLimiter(policy, time_func=lambda: 100.0)
    line_limiter.record_read(line_count=5)
    with pytest.raises(SourcePolicyError):
        line_limiter.record_read(line_count=4)


def test_source_read_rate_limiter_rejects_non_positive_line_counts():
    limiter = SourceReadRateLimiter(SourcePolicy(), time_func=lambda: 100.0)

    with pytest.raises(SourcePolicyError):
        limiter.record_read(line_count=0)
    with pytest.raises(SourcePolicyError):
        limiter.record_read(line_count=-5)


def test_source_read_rate_limiter_expires_old_events():
    now = 100.0
    policy = SourcePolicy(max_source_reads_per_10min=1)
    limiter = SourceReadRateLimiter(policy, window_seconds=10.0, time_func=lambda: now)

    limiter.record_read(line_count=1)
    now = 111.0
    limiter.record_read(line_count=1)


def test_source_read_rate_limiter_detects_adjacent_bulk_reads():
    policy = SourcePolicy(
        max_source_reads_per_10min=20,
        max_total_lines_per_10min=200,
        max_adjacent_reads_per_path_per_10min=2,
    )
    limiter = SourceReadRateLimiter(policy, time_func=lambda: 100.0)

    limiter.record_read(line_count=5, path="src/core/cache.py", start_line=1, end_line=5)
    limiter.record_read(line_count=5, path="src/core/cache.py", start_line=6, end_line=10)

    with pytest.raises(SourcePolicyError, match="bulk export"):
        limiter.record_read(line_count=5, path="src/core/cache.py", start_line=11, end_line=15)


def test_source_read_rate_limiter_detects_path_coverage_bulk_reads():
    policy = SourcePolicy(
        max_source_reads_per_10min=20,
        max_total_lines_per_10min=200,
        max_distinct_paths_per_10min=1,
    )
    limiter = SourceReadRateLimiter(policy, time_func=lambda: 100.0)

    limiter.record_read(line_count=1, path="A.py", start_line=1, end_line=1)

    with pytest.raises(SourcePolicyError, match="bulk export"):
        limiter.record_read(line_count=1, path="B.py", start_line=1, end_line=1)
