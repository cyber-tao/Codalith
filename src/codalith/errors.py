"""Domain exceptions for Codalith."""


class CodalithError(Exception):
    """Base error for expected Codalith failures."""


class ConfigurationError(CodalithError):
    """Raised when a config file is missing or invalid."""


class CorpusNotFoundError(CodalithError):
    """Raised when a requested engine or project corpus is unknown."""


class URIResolutionError(CodalithError):
    """Raised when an external corpus URI is invalid or unsupported."""


class SourcePolicyError(CodalithError):
    """Raised when source access policy denies a read."""


class CodeRAGAdapterError(CodalithError):
    """Raised when a CodeRAG operation cannot be completed."""
