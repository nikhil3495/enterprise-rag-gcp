"""In-app authentication (defence in depth behind Cloud Run IAM).

Cloud Run is deployed with ``--no-allow-unauthenticated`` so Google's front end already rejects callers
without ``roles/run.invoker``. With ``RAG_AUTH_MODE=iam`` the app additionally verifies the Google-signed
ID token (signature, expiry, issuer, audience = service URL) and optionally enforces a principal allowlist.
``none`` is for local development only.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from fastapi import HTTPException, Request, status

from rag.config import Settings

log = logging.getLogger(__name__)

GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

TokenVerifier = Callable[[str, list[str]], dict[str, Any]]


@dataclass(frozen=True)
class Principal:
    email: str | None
    subject: str
    authenticated: bool


ANONYMOUS = Principal(email=None, subject="anonymous", authenticated=False)


class _CachedRequest:
    """google-auth transport whose cert fetches are cached (certs rotate daily; avoid a fetch per request)."""

    def __init__(self) -> None:
        import requests
        from google.auth.transport.requests import Request as GoogleRequest

        self._session = requests.Session()
        self._inner = GoogleRequest(session=self._session)
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def __call__(self, url: str, method: str = "GET", **kwargs: Any) -> Any:
        if method != "GET":
            return self._inner(url, method=method, **kwargs)
        with self._lock:
            if url in self._cache:
                return self._cache[url]
        resp = self._inner(url, method=method, **kwargs)
        if resp.status == 200:
            with self._lock:
                self._cache[url] = resp
        return resp

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_transport: _CachedRequest | None = None


def google_verifier(token: str, audience: list[str]) -> dict[str, Any]:
    global _transport
    from google.oauth2 import id_token

    if _transport is None:
        _transport = _CachedRequest()
    try:
        return dict(id_token.verify_token(token, cast(Any, _transport), audience=audience))
    except ValueError:
        # Possibly rotated certs: refresh once.
        _transport.clear()
        return dict(id_token.verify_token(token, cast(Any, _transport), audience=audience))


class Authenticator:
    def __init__(self, settings: Settings, verifier: TokenVerifier | None = None) -> None:
        self.mode = settings.auth_mode
        self.audience = [a.strip() for a in settings.auth_audience.split(",") if a.strip()]
        self.allowed = settings.allowed_principals
        self.verifier = verifier or google_verifier
        if self.mode == "iam" and not self.audience:
            raise ValueError("RAG_AUTH_AUDIENCE (the service URL) is required when RAG_AUTH_MODE=iam")

    def __call__(self, request: Request) -> Principal:
        if self.mode == "none":
            return ANONYMOUS
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "missing bearer token", headers={"WWW-Authenticate": "Bearer"}
            )
        try:
            claims = self.verifier(token.strip(), self.audience)
        except Exception as e:
            log.info("token verification failed", extra={"reason": str(e)[:200]})
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "invalid token", headers={"WWW-Authenticate": "Bearer"}
            ) from e
        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token issuer")
        email = str(claims.get("email", "")).lower() or None
        if self.allowed and (
            not email or not claims.get("email_verified", False) or email not in self.allowed
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "principal not allowed")
        principal = Principal(email=email, subject=str(claims.get("sub", "")), authenticated=True)
        request.state.principal = principal
        return principal
