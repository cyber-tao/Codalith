from __future__ import annotations

import os

from jobs.coderag_acceptance import (
    configure_openai_compatible_env,
    minimal_coderag_dependencies,
)


def test_openai_compatible_env_aliases(monkeypatch):
    for key in (
        "API_KEY",
        "BASE_URL",
        "MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "CODERAG_OPENAI_MODEL",
        "CODERAG_CHAT_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("MODEL", "text-embedding-test")

    configure_openai_compatible_env()

    assert os.environ["OPENAI_API_KEY"] == "test-key"
    assert os.environ["OPENAI_BASE_URL"] == "https://example.test/v1"
    assert os.environ["CODERAG_OPENAI_MODEL"] == "text-embedding-test"
    assert os.environ["CODERAG_CHAT_MODEL"] == "text-embedding-test"


def test_openai_provider_installs_openai_sdk_dependency():
    assert "openai>=2.41.1,<3" in minimal_coderag_dependencies("openai")
    assert "openai>=2.41.1,<3" not in minimal_coderag_dependencies("fake")
