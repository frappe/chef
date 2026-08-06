"""Bearer-token auth for the Chef API.

v1 auth is a single static token (``settings.api_token``). :func:`require_token` is a
FastAPI dependency mounted on the protected routers; a small set of discovery/doc paths
(``/healthz``, ``/llms.txt``, ``/openapi.json``, ``/docs`` …) are exempt so agents and
humans can read the API before authenticating.

The dependency also declares an HTTP-Bearer security scheme, so ``/docs`` shows an
*Authorize* button and the OpenAPI advertises the scheme to generated clients.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from chef.config import Settings, get_settings

#: Paths reachable without a token — discovery, health and the interactive docs.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/",
        "/healthz",
        "/llms.txt",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/favicon.ico",
    }
)

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="ChefToken",
    description="Static API bearer token (v1). Send `Authorization: Bearer <token>`.",
)


def is_public_path(path: str) -> bool:
    """True for paths served without authentication (see :data:`PUBLIC_PATHS`)."""
    if path in PUBLIC_PATHS:
        return True
    # Swagger UI / ReDoc assets live under these prefixes.
    return path.startswith(("/docs", "/redoc"))


async def require_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    """Reject requests whose bearer token does not match ``settings.api_token``.

    Public paths are always allowed; everything else needs the exact token. Raises a
    ``401`` (rendered as :class:`~chef.schemas.ErrorOut` by the app's exception handler).
    """
    if is_public_path(request.url.path):
        return
    token = credentials.credentials if credentials else None
    if not token or token != settings.api_token:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
