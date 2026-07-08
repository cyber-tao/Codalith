"""Authentication and scope helpers for Codalith gateway execution."""

from __future__ import annotations

import contextvars
import hmac
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from codalith.corpus.registry import CorpusRegistry
from codalith.errors import CodalithError

# Shared default for AuthContext.local and audit records when no client is known.
DEFAULT_CLIENT_ID = "codex"

# Capability scopes every deployment needs; corpus access scopes come from the registry.
_BASE_SCOPES = frozenset({"source:read", "index:status", "cards:read", "graph:read"})


def default_scopes(registry: CorpusRegistry | None = None) -> frozenset[str]:
    """Scopes granted when CODALITH_SCOPES is unset: base capabilities plus every
    access scope declared by the configured corpora (self-hosted default)."""
    scopes = set(_BASE_SCOPES)
    if registry is not None:
        for collection in (registry.corpora, registry.projects, registry.generated):
            for corpus in collection.values():
                scopes |= corpus.access_scopes
    return frozenset(scopes)


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: str
    session_id: str
    client: str
    scopes: frozenset[str]

    @classmethod
    def local(cls, fallback_scopes: Iterable[str] | None = None) -> AuthContext:
        return cls(
            user_id=os.getenv("CODALITH_USER_ID", "local-user"),
            session_id=os.getenv("CODALITH_SESSION_ID", "local-session"),
            client=os.getenv("CODALITH_CLIENT_ID", DEFAULT_CLIENT_ID),
            scopes=frozenset(scopes_from_env(fallback_scopes)),
        )


class AuthError(CodalithError):
    """Raised when a request cannot be authenticated or lacks a required scope."""


_CURRENT_AUTH: contextvars.ContextVar[AuthContext | None] = contextvars.ContextVar(
    "codalith_auth_context",
    default=None,
)


def scopes_from_env(fallback: Iterable[str] | None = None) -> set[str]:
    raw = os.getenv("CODALITH_SCOPES", "")
    if raw.strip():
        return {item.strip() for item in raw.split(",") if item.strip()}
    return set(fallback) if fallback is not None else set(_BASE_SCOPES)


def current_auth_context(default: AuthContext | None = None) -> AuthContext:
    return _CURRENT_AUTH.get() or default or AuthContext.local()


def set_current_auth_context(auth: AuthContext) -> contextvars.Token[AuthContext | None]:
    return _CURRENT_AUTH.set(auth)


def reset_current_auth_context(token: contextvars.Token[AuthContext | None]) -> None:
    _CURRENT_AUTH.reset(token)


def authenticate_http_headers(
    headers: Mapping[str, str],
    fallback_scopes: Iterable[str] | None = None,
) -> AuthContext:
    expected_token = os.getenv("CODALITH_HTTP_BEARER_TOKEN", "").strip()
    identity_header = os.getenv("CODALITH_HTTP_IDENTITY_HEADER", "").strip()

    if expected_token:
        authorization = headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected_token):
            raise AuthError("Missing or invalid bearer token")

    if identity_header:
        user_id = headers.get(identity_header, "").strip()
        if not user_id:
            raise AuthError(f"Missing trusted identity header: {identity_header}")
    else:
        user_id = os.getenv("CODALITH_HTTP_USER_ID", "http-user")

    return AuthContext(
        user_id=user_id,
        session_id=headers.get("MCP-Session-Id", "http-session"),
        client=headers.get("User-Agent", "mcp-http"),
        scopes=frozenset(scopes_from_env(fallback_scopes)),
    )
