"""Health + readiness routes — exempt from bearer auth.

``/health`` is the liveness probe (cheap). ``/ready`` confirms the agent
registry is populated and the tech stack config parsed.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.agent_registry import get_registry
from core.config_loader import get_config

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    cfg = get_config()
    registry = get_registry()
    agents = [e.agent_id for e in registry.all_entries()]
    if not agents:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "detail": "no agents registered",
                "known_agent_configs": cfg.agent_ids,
            },
        )
    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "agents": agents,
            "configs_dir": str(cfg.configs_dir),
        },
    )
