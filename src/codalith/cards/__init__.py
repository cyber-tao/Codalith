"""Knowledge card schema, rendering, generation, and verification."""

# Directory name under a corpus card_root/indexed_root where rendered cards
# live. Retrieval layers treat any hit inside this directory as a card.
CARDS_DIR = "KNOWLEDGE"


def is_card_path(path: str) -> bool:
    """Whether a corpus-relative path points into the rendered cards directory."""
    return CARDS_DIR in path.split("/")
