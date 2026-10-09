"""In-tree hybrid retrieval engine.

The indexer, chunker, embedding providers, and LanceDB store live here. Generation
binding stays in ``codalith.indexing``. Importing this package does not load LanceDB
or an embedding model.
"""

ENGINE_VERSION = "1.0.0"

__all__ = ["ENGINE_VERSION"]
