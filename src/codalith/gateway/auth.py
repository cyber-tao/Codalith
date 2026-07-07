"""Authentication and scope helpers for Codalith gateway execution."""

from __future__ import annotations

import contextvars
import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass

from codalith.errors import CodalithError


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: str
    session_id: str
    client: str
    scopes: frozenset[str]

    @classmethod
    def local(cls) -> AuthContext:
        return cls(
            user_id=os.getenv("CODALITH_USER_ID", "local-user"),
            session_id=os.getenv("CODALITH_SESSION_ID", "local-session"),
            client=os.getenv("CODALITH_CLIENT_ID", "codex"),
            scopes=frozenset(scopes_from_env()),
        )


class AuthError(CodalithError):
    """Raised when a request cannot be authenticated."""


_CURRENT_AUTH: contextvars.ContextVar[AuthContext | None] = contextvars.ContextVar(
    "codalith_auth_context",
    default=None,
)


def scopes_from_env() -> set[str]:
    raw = os.getenv("CODALITH_SCOPES", "source:read,index:status,cards:read,graph:read,ue:5.7")
    return {item.strip() for item in raw.split(",") if item.strip()}


def current_auth_context(default: AuthContext | None = None) -> AuthContext:
    return _CURRENT_AUTH.get() or default or AuthContext.local()


def set_current_auth_context(auth: AuthContext) -> contextvars.Token[AuthContext | None]:
    return _CURRENT_AUTH.set(auth)


def reset_current_auth_context(token: contextvars.Token[AuthContext | None]) -> None:
    _CURRENT_AUTH.reset(token)


def authenticate_http_headers(headers: Mapping[str, str]) -> AuthContext:
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
        scopes=frozenset(scopes_from_env()),
    )
