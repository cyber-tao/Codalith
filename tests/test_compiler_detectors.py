from __future__ import annotations

import json

import pytest

from codalith.compiler.entity_detector import detect_identifiers, detect_modules
from codalith.compiler.intent_detector import detect_intent
from codalith.compiler.source_locator import (
    SourcePrior,
    module_hints,
    reset_domain_config_cache,
    source_priors,
)
from codalith.corpus.uris import module_uri, source_uri, symbol_uri
from codalith.errors import ConfigurationError


def test_detect_intent_requires_word_boundaries_for_ascii_terms():
    assert detect_intent("The terror of templates") == "explain"
    assert detect_intent("Why does packaging error out") == "debug"
    assert detect_intent("Game crash on startup") == "debug"


def test_detect_intent_keeps_substring_semantics_for_cjk_terms():
    assert detect_intent("打包时报错了") == "debug"


def test_detect_intent_prefers_explicit_mode():
    assert detect_intent("Why does packaging error out", explicit="trace") == "trace"


def test_detect_identifiers_filters_english_question_words():
    assert detect_identifiers("How does AActor tick") == ["AActor"]
    assert detect_identifiers("Where is FVector defined") == ["FVector"]
    assert detect_identifiers("Explain This Please") == ["Please"]


def test_detect_modules_uses_word_boundaries():
    assert "Net" not in detect_modules("network replication troubleshooting")
    assert "Net" in detect_modules("the Net module handles replication")


def test_detect_modules_matches_spaced_camel_case_variants():
    modules = detect_modules("enhanced input mapping context not firing")
    assert "EnhancedInput" in modules
    assert "NetCore" in detect_modules("net core serialization internals")


def test_source_priors_load_from_bundled_config():
    priors = source_priors()
    assert priors
    assert all(isinstance(prior, SourcePrior) for prior in priors)
    assert any(prior.path.endswith("Actor.h") for prior in priors)


def test_source_priors_respect_environment_override(tmp_path, monkeypatch):
    override = tmp_path / "priors.json"
    override.write_text(
        json.dumps(
            {
                "priors": [
                    {
                        "path": "Engine/Source/Custom/File.h",
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
        priors = source_priors()
        assert len(priors) == 1
        assert priors[0].title == "Custom"
        assert priors[0].line_terms == ()
        # Domain vocabulary is optional; an override without hints yields none.
        assert module_hints() == frozenset()
    finally:
        reset_domain_config_cache()


def test_source_priors_reject_empty_dataset(tmp_path, monkeypatch):
    override = tmp_path / "empty.json"
    override.write_text(json.dumps({"priors": []}), encoding="utf-8")
    monkeypatch.setenv("CODALITH_SOURCE_PRIORS", str(override))
    reset_domain_config_cache()
    try:
        with pytest.raises(ConfigurationError):
            source_priors()
    finally:
        reset_domain_config_cache()


def test_corpus_uris_are_scheme_uniform_across_corpus_kinds():
    assert source_uri("ue-5.7.4", "A.h", 1, 5) == "codalith://ue-5.7.4/source/A.h#L1-L5"
    assert source_uri("ProjectA", "A.h", 1, 5) == "codalith://ProjectA/source/A.h#L1-L5"
    assert (
        source_uri("generated-ue-5.7.4", "Saved/Logs/Editor.log", 1, 5)
        == "codalith://generated-ue-5.7.4/source/Saved/Logs/Editor.log#L1-L5"
    )
    assert module_uri("ue-5.7.4", "Engine") == "codalith://ue-5.7.4/module/Engine"
    assert symbol_uri("ue-5.7.4", "AActor") == "codalith://ue-5.7.4/symbol/AActor"
