"""Bearer-token auth middleware.

Per ``ADLC_Tech_Stack_Config.json#api.auth`` ADLC accepts a single static
bearer token sourced from ``$ADLC_API_KEY``. GenWiz includes it on every
call. There is no per-user auth — GenWiz is the only client and the token
is rotated environment-wide.

Health endpoints are exempt so liveness probes don't need the key.
"""

from __future__ import annotations

import os
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


_API_KEY_ENV = "ADLC_API_KEY"
_AUTH_HEADER = "Authorization"
_BEARER_PREFIX = "Bearer "
_PUBLIC_SUFFIX = "/.well-known/agent-card.json"


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Reject any request without a matching ``Authorization: Bearer …``."""

    def __init__(self, app, exempt_paths: Iterable[str] = ()) -> None:
        super().__init__(app)
        self._exempt = tuple(exempt_paths)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(self._exempt) or path.endswith(_PUBLIC_SUFFIX):
            return await call_next(request)

        expected = os.environ.get(_API_KEY_ENV)
        if not expected:
            return JSONResponse(
                status_code=500,
                content={
                    "error": "server_misconfigured",
                    "detail": f"{_API_KEY_ENV} is not set",
                },
            )

        header_value = request.headers.get(_AUTH_HEADER, "")
        if not header_value.startswith(_BEARER_PREFIX):
            return JSONResponse(
                status_code=401,
                content={"error": "missing_bearer_token"},
            )

        token = header_value[len(_BEARER_PREFIX):].strip()
        if token != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "invalid_bearer_token"},
            )

        return await call_next(request)
