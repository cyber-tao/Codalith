from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from codalith.corpus.registry import Corpus
from codalith.corpus.source_policy import SourcePolicy
from codalith.indexing.semantic import (
    _filesystem_path,
    _materialize_source_view,
    prepare_semantic_index,
)
from codalith.semantic.engine import SemanticEngine
from codalith.semantic.types import IndexStats

_CONTENT = b"def example():\n    return 42\n"
_RELATIVE = "Engine/" + "/".join(["nested-source-directory"] * 7) + "/example.py"


@pytest.fixture
def long_path_root(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path
    shutil.rmtree(_filesystem_path(tmp_path))


@pytest.mark.parametrize("force_copy", [False, True], ids=["hardlink", "copy_fallback"])
def test_materialization_supports_destinations_longer_than_max_path(
    long_path_root: Path, monkeypatch: pytest.MonkeyPatch, force_copy: bool
) -> None:
    source = long_path_root / "source"
    view = long_path_root / ("source-view-" + "x" * 80)
    original = _filesystem_path(source / _RELATIVE)
    original.parent.mkdir(parents=True)
    original.write_bytes(_CONTENT)
    view.mkdir()
    destination = view / _RELATIVE
    assert len(str(destination)) > 260
    link_paths: list[tuple[str, str]] = []
    original_link = os.link

    def link(source_path: Path, destination_path: Path) -> None:
        link_paths.append((str(source_path), str(destination_path)))
        if force_copy:
            raise OSError("Hardlinks unavailable across volumes")
        original_link(source_path, destination_path)

    def reject_symlink(*args: object, **kwargs: object) -> None:
        raise OSError("Symlinks unavailable")

    monkeypatch.setattr("codalith.indexing.semantic.os.link", link)
    if force_copy:
        monkeypatch.setattr(Path, "symlink_to", reject_symlink)
    _materialize_source_view(source, view, (_RELATIVE,))
    materialized = _filesystem_path(destination)
    assert materialized.read_bytes() == _CONTENT
    assert not materialized.is_symlink()
    assert os.path.samestat(original.stat(), materialized.stat()) is not force_copy
    assert len(link_paths) == 1
    if os.name == "nt":
        assert all(path.startswith("\\\\?\\") for path in link_paths[0])


def test_long_materialized_files_can_be_indexed_and_the_temporary_view_is_cleaned(
    long_path_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = long_path_root / "source"
    index_root = long_path_root / "index"
    index_root.mkdir()
    original = _filesystem_path(source / _RELATIVE)
    original.parent.mkdir(parents=True)
    original.write_bytes(_CONTENT)
    corpus = Corpus(
        corpus_id="long-path",
        display_name="Long-path corpus",
        description="Materialization regression fixture",
        revision="test-v1",
        source_root=source,
        index_root=index_root,
        semantic_store=None,
        adapter="python",
        embedding_provider="fake",
        embedding_model="",
        include_extensions=(".py",),
        exclude_globs=(),
    )
    observed_views: list[Path] = []
    original_index = SemanticEngine.index

    def index(engine: SemanticEngine, source_dir: Path) -> IndexStats:
        observed_views.append(source_dir)
        assert len(str(source_dir / _RELATIVE)) > 260
        if os.name == "nt":
            assert str(source_dir).startswith("\\\\?\\")
        return original_index(engine, source_dir)

    monkeypatch.setattr(SemanticEngine, "index", index)
    info = prepare_semantic_index(
        corpus,
        long_path_root / "store",
        SourcePolicy(200, 2000, 1_000_000, (".env",)),
        mode="build",
        indexed_files={_RELATIVE: hashlib.sha256(_CONTENT).hexdigest()},
    )
    assert (info.total_files, info.covered_files, info.extra_files) == (1, 1, 0)
    assert info.total_chunks > 0
    assert len(observed_views) == 1
    assert not observed_views[0].exists()
    assert list(index_root.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length namespace")
def test_extended_paths_are_idempotent_and_support_unc_paths() -> None:
    prefixed = Path("\\\\?\\C:\\source\\example.py")
    assert _filesystem_path(prefixed) == prefixed
    assert _filesystem_path(Path("\\\\server\\share\\source\\example.py")) == Path(
        "\\\\?\\UNC\\server\\share\\source\\example.py"
    )
