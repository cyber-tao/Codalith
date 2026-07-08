from __future__ import annotations

import json

import pytest

from codalith.compiler.entity_detector import detect_identifiers, detect_modules
from codalith.compiler.intent_detector import detect_intent
from codalith.compiler.source_locator import (
    SourcePrior,
    load_source_domain_config,
    reset_domain_config_cache,
)
from codalith.corpus.uris import card_uri, module_uri, parse_source_uri, source_uri, symbol_uri
from codalith.errors import ConfigurationError


def test_detect_intent_requires_word_boundaries_for_ascii_terms():
    assert detect_intent("The terror of templates") == "explain"
    assert detect_intent("Why does packaging error out") == "debug"
    assert detect_intent("Application crash on startup") == "debug"


def test_detect_intent_keeps_substring_semantics_for_cjk_terms():
    assert detect_intent("打包时报错了") == "debug"


def test_detect_intent_prefers_explicit_mode():
    assert detect_intent("Why does packaging error out", explicit="trace") == "trace"


def test_detect_identifiers_filters_english_question_words():
    assert detect_identifiers("How does CachedValue expire") == ["CachedValue"]
    assert detect_identifiers("Where is EventBus defined") == ["EventBus"]
    assert detect_identifiers("Explain This Please") == ["Please"]


def test_detect_identifiers_matches_snake_case_symbols():
    assert detect_identifiers("How does cache_value compute ttl") == ["cache_value"]
    assert detect_identifiers("Where is read_source implemented") == ["read_source"]
    # Plain lowercase words never become identifiers.
    assert detect_identifiers("how does caching work") == []


def test_detect_modules_uses_explicit_hints_and_word_boundaries():
    hints = frozenset({"Net", "EventBus", "CoreCache"})
    assert "Net" not in detect_modules("network replication troubleshooting", module_hints=hints)
    assert "Net" in detect_modules("the Net module handles replication", module_hints=hints)


def test_detect_modules_matches_spaced_camel_case_variants():
    hints = frozenset({"EventBus", "CoreCache"})
    assert "EventBus" in detect_modules("event bus dispatch", module_hints=hints)
    assert "CoreCache" in detect_modules("core cache ttl internals", module_hints=hints)


def test_source_priors_load_from_explicit_config(source_priors_path):
    config = load_source_domain_config(source_priors_path)
    assert config.priors
    assert all(isinstance(prior, SourcePrior) for prior in config.priors)
    assert any(prior.path.endswith("cache.py") for prior in config.priors)
    assert config.module_hints == frozenset({"core"})


def test_source_priors_do_not_use_global_environment_override(tmp_path, monkeypatch, source_priors_path):
    override = tmp_path / "priors.json"
    override.write_text(
        json.dumps(
            {
                "priors": [
                    {
                        "path": "src/custom/file.py",
                        "title": "Custom",
                        "module": "Custom",
                        "triggers": ["custom"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODALITH_SOURCE_PRIORS", str(override))
    reset_domain_config_cache()
    try:
        priors = load_source_domain_config(source_priors_path).priors
        assert len(priors) == 2
        assert all(prior.title != "Custom" for prior in priors)
        assert load_source_domain_config(None).priors == ()
    finally:
        reset_domain_config_cache()


def test_source_priors_reject_malformed_dataset(tmp_path):
    override = tmp_path / "bad.json"
    override.write_text(json.dumps({"priors": {}}), encoding="utf-8")
    reset_domain_config_cache()
    try:
        with pytest.raises(ConfigurationError):
            load_source_domain_config(override)
    finally:
        reset_domain_config_cache()


def test_corpus_uris_are_scheme_uniform_across_corpus_kinds():
    assert source_uri("sample-codebase", "A.py", 1, 5) == "codalith://sample-codebase/source/A.py#L1-L5"
    assert source_uri("SampleProject", "A.py", 1, 5) == "codalith://SampleProject/source/A.py#L1-L5"
    assert (
        source_uri("generated-sample", "generated/build.log", 1, 5)
        == "codalith://generated-sample/source/generated/build.log#L1-L5"
    )
    assert module_uri("sample-codebase", "core") == "codalith://sample-codebase/module/core"
    assert symbol_uri("sample-codebase", "CachedValue") == "codalith://sample-codebase/symbol/CachedValue"
    assert card_uri("sample-codebase", "module", "core-cache") == "codalith://sample-codebase/card/module/core-cache"


def test_parse_source_uri_round_trips_and_rejects_other_facets():
    uri = source_uri("sample-codebase", "src/core/cache.py", 2, 9)
    assert parse_source_uri(uri) == ("sample-codebase", "src/core/cache.py", 2, 9)
    assert parse_source_uri(module_uri("sample-codebase", "core")) is None
    assert parse_source_uri("https://example.com/source/A.py#L1-L2") is None
