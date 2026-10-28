"""In-tree hybrid retrieval engine.

Chunking, embedding providers, the LanceDB store, and dense+BM25 search live here.
Generation binding stays in ``codalith.indexing``. Importing this package does not load
LanceDB or an embedding model.
"""

# Raising ENGINE_VERSION invalidates every semantic generation; bump it whenever chunking,
# embedding input, or the store layout changes.
ENGINE_VERSION = "2.0.0"

__all__ = ["ENGINE_VERSION"]
