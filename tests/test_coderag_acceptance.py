from __future__ import annotations

import os

from jobs.coderag_acceptance import (
    acceptance_minimums,
    configure_coderag_runtime_env,
    minimal_coderag_dependencies,
)


def test_configures_openai_embedding_runtime_env(monkeypatch):
    for key in (
        "CODALITH_CODERAG_EMBEDDING_MODEL",
        "CODALITH_CODERAG_EMBEDDING_BATCH_SIZE",
        "CODALITH_CODERAG_CHAT_MODEL",
        "CODALITH_CODERAG_WORKERS",
        "CODERAG_OPENAI_MODEL",
        "CODERAG_OPENAI_BATCH",
        "CODERAG_CHAT_MODEL",
        "CODERAG_WORKERS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODALITH_CODERAG_EMBEDDING_MODEL", "embedding-test")
    monkeypatch.setenv("CODALITH_CODERAG_EMBEDDING_BATCH_SIZE", "7")
    monkeypatch.setenv("CODALITH_CODERAG_CHAT_MODEL", "chat-test")
    monkeypatch.setenv("CODALITH_CODERAG_WORKERS", "3")

    configure_coderag_runtime_env("openai")

    assert os.environ["CODERAG_PROVIDER"] == "openai"
    assert os.environ["CODERAG_INDEX_ALL_TEXT"] == "1"
    assert os.environ["CODERAG_OPENAI_MODEL"] == "embedding-test"
    assert os.environ["CODERAG_OPENAI_BATCH"] == "7"
    assert os.environ["CODERAG_CHAT_MODEL"] == "chat-test"
    assert os.environ["CODERAG_WORKERS"] == "3"


def test_configures_fake_runtime_without_openai_models(monkeypatch):
    for key in (
        "CODERAG_OPENAI_MODEL",
        "CODERAG_OPENAI_BATCH",
        "CODERAG_CHAT_MODEL",
        "CODERAG_WORKERS",
    ):
        monkeypatch.delenv(key, raising=False)

    configure_coderag_runtime_env("fake")

    assert os.environ["CODERAG_PROVIDER"] == "fake"
    assert os.environ["CODERAG_INDEX_ALL_TEXT"] == "1"
    assert os.environ["CODERAG_WORKERS"] == "4"
    assert "CODERAG_OPENAI_MODEL" not in os.environ
    assert "CODERAG_OPENAI_BATCH" not in os.environ
    assert "CODERAG_CHAT_MODEL" not in os.environ


def test_openai_provider_installs_openai_sdk_dependency():
    assert "openai>=2.41.1,<3" in minimal_coderag_dependencies("openai")
    assert "openai>=2.41.1,<3" not in minimal_coderag_dependencies("fake")


def test_acceptance_minimums_keep_full_defaults():
    assert acceptance_minimums(None, None, None) == (1000, 1000)


def test_acceptance_minimums_allow_scoped_index_smoke():
    assert acceptance_minimums("/corpus/Actor.h", None, None) == (1, 1)
    assert acceptance_minimums("/corpus/Actor.h", 5, 10) == (5, 10)
