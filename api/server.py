"""FastAPI app factory.

Builds the application in one place so ``run.py``, tests, and any future
ASGI host share the same construction path:

1. Call ``agent_registry.bootstrap()`` so every agent self-registers.
2. Build the FastAPI instance.
3. Wire the bearer-token middleware (health endpoints exempt).
4. Import the agent router *after* bootstrap so routes are bound to the
   populated registry.

Logging level comes from ``$LOG_LEVEL`` (default ``INFO``) per the tech
stack config's environment block.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI

from api.middleware.auth import BearerTokenMiddleware
from core.agent_registry import bootstrap
from core.config_loader import get_config


def create_app() -> FastAPI:
    _configure_logging()
    bootstrap()

    cfg = get_config()
    tech_meta = cfg.tech_stack.get("tech_stack", {})

    app = FastAPI(
        title="ADLC Agent Service",
        version=tech_meta.get("version", "0.0.0"),
        description=tech_meta.get("description", ""),
    )

    app.add_middleware(
        BearerTokenMiddleware,
        exempt_paths=("/health", "/ready"),
    )

    # Import order matters: health, agents, and a2a all read the registry
    # at import time, so they must run after `bootstrap()`.
    from api.routers import a2a, agents, health

    app.include_router(health.router)
    app.include_router(agents.router)
    app.include_router(a2a.router)

    return app


def _configure_logging(level: Optional[str] = None) -> None:
    chosen = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, chosen, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )
    # Silence noisy google-genai AFC info messages
    logging.getLogger("google_genai.models").setLevel(logging.WARNING)


app = create_app()
