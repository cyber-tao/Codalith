from pathlib import Path

import numpy as np
import pytest

from codalith.semantic.store import LanceStore, SemanticStoreError, StoreProvenance
from codalith.semantic.types import Chunk


@pytest.mark.parametrize(
    "vectors",
    [None, np.ones((0, 3)), np.ones((2, 3)), np.ones((1, 2)), np.full((1, 3), np.nan)],
)
def test_invalid_vectors_do_not_record_a_file_or_partial_chunks(
    tmp_path: Path, vectors: np.ndarray | None
) -> None:
    store = LanceStore.create(tmp_path / "store", StoreProvenance("test", 3))
    chunk = Chunk("source", 1, 1, "cpp")
    try:
        with pytest.raises(SemanticStoreError):
            store.write_file("bad.cpp", "cpp", "digest", 0, 6, [chunk], vectors)
        assert store.stats().total_files == 0
        assert store.stats().total_chunks == 0
        assert store.file_hashes() == {}
        assert store.write_file("good.cpp", "cpp", "digest", 0, 6, [chunk], np.ones((1, 3))) == 1
        assert store.stats().total_files == 1
        assert store.stats().total_chunks == 1
    finally:
        store.close()
