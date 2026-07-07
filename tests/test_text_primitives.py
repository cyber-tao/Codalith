from __future__ import annotations

from codalith.text import camel_words, contains_word, normalize, token_set, tokenize


def test_normalize_lowercases_and_folds_hyphens():
    assert normalize("Build-Config UPROPERTY") == "build config uproperty"


def test_tokenize_returns_identifier_tokens_in_order():
    assert tokenize("How does AActor::BeginPlay tick?") == [
        "how",
        "does",
        "aactor",
        "beginplay",
        "tick",
    ]


def test_tokenize_skips_bare_numbers_but_keeps_numeric_tails():
    assert tokenize("UE 5.7.4 int32 replication") == ["ue", "int32", "replication"]


def test_tokenize_honors_min_length():
    assert tokenize("a an the FName", min_length=2) == ["an", "the", "fname"]


def test_token_set_deduplicates():
    assert token_set("actor Actor ACTOR") == {"actor"}


def test_contains_word_requires_boundaries_for_ascii_terms():
    assert not contains_word("error", "the terror of templates")
    assert contains_word("error", "packaging error out")
    assert contains_word("call path", "show the call path for tick")
    assert not contains_word("net", "network replication")


def test_contains_word_keeps_substring_semantics_for_cjk_terms():
    assert contains_word("报错", "打包时报错了")


def test_camel_words_splits_capitalized_runs():
    assert camel_words("NetCore") == ["Net", "Core"]
    assert camel_words("EnhancedInput") == ["Enhanced", "Input"]
    assert camel_words("UObject") == ["Object"]
