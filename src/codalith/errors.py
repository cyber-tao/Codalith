"""Domain exceptions for Codalith."""


class CodalithError(Exception):
    """Base error for expected Codalith failures."""


class ConfigurationError(CodalithError):
    """Raised when a config file is missing or invalid."""


class CorpusNotFoundError(CodalithError):
    """Raised when a requested base, project, or generated corpus is unknown."""


class CorpusResolutionError(CodalithError):
    """Raised when corpus and overlay selectors describe incompatible targets."""


class URIResolutionError(CodalithError):
    """Raised when an external corpus URI is invalid or unsupported."""


class SourcePolicyError(CodalithError):
    """Raised when source access policy denies a read."""


class SourceReadError(CodalithError):
    """Raised when a corpus source file cannot be read (missing or invalid path)."""


class CodeRAGAdapterError(CodalithError):
    """Raised when a CodeRAG operation cannot be completed."""
