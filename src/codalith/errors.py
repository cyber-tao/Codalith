"""Domain exceptions for Codalith."""


class CodalithError(Exception):
    """Base error for expected Codalith failures."""


class ConfigurationError(CodalithError):
    """Raised when a config file is missing or invalid."""


class CorpusNotFoundError(CodalithError):
    """Raised when a requested corpus or workspace is unknown."""


class URIResolutionError(CodalithError):
    """Raised when an external corpus URI is invalid or unsupported."""


class SourcePolicyError(CodalithError):
    """Raised when source access policy denies a read."""


class SourceReadError(CodalithError):
    """Raised when a corpus source file cannot be read (missing or invalid path)."""


class IndexUnavailableError(CodalithError):
    """Raised when an index generation is missing, stale, or invalid."""


class IndexBuildError(CodalithError):
    """Raised when an index generation cannot be built or published."""


class RetrievalError(CodalithError):
    """Raised when a requested retrieval strategy cannot be completed."""
