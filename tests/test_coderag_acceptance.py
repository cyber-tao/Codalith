from jobs.coderag_acceptance import (
    acceptance_minimums,
    minimal_coderag_dependencies,
)


def test_openai_provider_installs_openai_sdk_dependency():
    assert "openai>=2.41.1,<3" in minimal_coderag_dependencies("openai")
    assert "openai>=2.41.1,<3" not in minimal_coderag_dependencies("fake")


def test_acceptance_minimums_keep_full_defaults():
    assert acceptance_minimums(None, None, None) == (1000, 1000)


def test_acceptance_minimums_allow_scoped_index_smoke():
    assert acceptance_minimums("/corpus/Actor.h", None, None) == (1, 1)
    assert acceptance_minimums("/corpus/Actor.h", 5, 10) == (5, 10)
