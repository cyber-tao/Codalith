"""Domain exceptions and their public error contract."""

INTERNAL_ERROR_CODE = "internal"
INVALID_ARGUMENTS_CODE = "invalid_arguments"


class CodalithError(Exception):
    """Base error for expected Codalith failures."""

    code = "codalith_error"
    retryable = False


class ConfigurationError(CodalithError):
    """Raised when a config file is missing or invalid."""

    code = "configuration_invalid"


class CorpusNotFoundError(CodalithError):
    """Raised when a requested corpus or workspace is unknown."""

    code = "corpus_not_found"


class URIResolutionError(CodalithError):
    """Raised when an external corpus URI is invalid or unsupported."""

    code = "invalid_uri"


class SourcePolicyError(CodalithError):
    """Raised when source access policy denies a read."""

    code = "source_policy"


class SourceReadError(CodalithError):
    """Raised when a corpus source file cannot be read (missing or invalid path)."""

    code = "source_unreadable"


class SymbolNotFoundError(CodalithError):
    """Raised when a symbol id is not part of the active generation."""

    code = "symbol_not_found"


class IndexUnavailableError(CodalithError):
    """Raised when no usable index generation can serve a request."""

    code = "index_unavailable"


class IndexMissingError(IndexUnavailableError):
    """Raised when a corpus has no published index generation."""

    code = "index_missing"


class IndexInvalidError(IndexUnavailableError):
    """Raised when the active generation no longer matches its artifacts or configuration."""

    code = "index_invalid"


class IndexBuildError(CodalithError):
    """Raised when an index generation cannot be built or published."""

    code = "index_build_failed"


class RetrievalError(CodalithError):
    """Raised when a requested retrieval strategy cannot be completed."""

    code = "retrieval_failed"
    retryable = True


def describe_error(error: BaseException) -> tuple[str, bool]:
    """Return the public ``(code, retryable)`` pair for any tool failure."""

    if isinstance(error, CodalithError):
        return error.code, error.retryable
    if isinstance(error, (ValueError, TypeError)):
        return INVALID_ARGUMENTS_CODE, False
    return INTERNAL_ERROR_CODE, True
