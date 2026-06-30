"""Domain exceptions for UE Context Engine."""


class UEContextError(Exception):
    """Base error for expected UE Context failures."""


class ConfigurationError(UEContextError):
    """Raised when a config file is missing or invalid."""


class CorpusNotFoundError(UEContextError):
    """Raised when a requested engine or project corpus is unknown."""


class URIResolutionError(UEContextError):
    """Raised when an external UE URI is invalid or unsupported."""


class SourcePolicyError(UEContextError):
    """Raised when source access policy denies a read."""


class CodeRAGAdapterError(UEContextError):
    """Raised when a CodeRAG operation cannot be completed."""
