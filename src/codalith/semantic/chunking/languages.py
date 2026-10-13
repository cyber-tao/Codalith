"""File-extension -> language mapping and the languages with symbol parsers."""

from __future__ import annotations

from pathlib import Path

# Languages with symbol-aware spans (function/class/method). Python uses the stdlib
# ``ast``; the rest use tree-sitter. Every other language is chunked into line windows.
PYTHON = "python"
TREE_SITTER_LANGUAGES = {"javascript", "typescript", "tsx", "go", "rust", "java"}

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".md": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".cfg": "ini",
    ".ini": "ini",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".vue": "vue",
    ".svelte": "svelte",
    ".properties": "properties",
    ".gradle": "gradle",
}

# Well-known text files without a conventional extension, matched on the lowercased name.
FILENAME_TO_LANGUAGE = {
    "dockerfile": "dockerfile",
    "makefile": "make",
    "license": "text",
    "notice": "text",
    "readme": "text",
    "codeowners": "text",
    ".env": "text",
    ".gitignore": "text",
    ".dockerignore": "text",
}


def detect_language(path: str | Path) -> str:
    """Return the chunking language for ``path``; unrecognized files are plain ``"text"``."""

    p = Path(path)
    return (
        EXTENSION_TO_LANGUAGE.get(p.suffix.lower())
        or FILENAME_TO_LANGUAGE.get(p.name.lower())
        or "text"
    )
