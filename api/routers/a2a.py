"""A2A protocol routes.

For now this binds one Agent Card discovery route per registered agent,
at ``{agent_endpoint}/.well-known/agent-card.json`` per the A2A spec.
JSON-RPC (``message/send``, ``tasks/get``, etc.) is a follow-up — once
the card is published, the dispatch route can be added without
re-shaping the surface.

Routes inherit the bearer-token middleware from :mod:`api.server` — A2A
discovery is *not* public in this service. Flip the middleware's
``exempt_paths`` to make it public if you want anonymous discovery.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Request

from core.a2a.agent_card import build_agent_card
from core.agent_registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter()


def _make_card_handler(agent_id: str):
    async def handler(request: Request) -> Dict[str, Any]:
        base_url = f"{request.url.scheme}://{request.url.netloc}"
        return build_agent_card(agent_id, base_url)
    return handler


def _register_routes() -> None:
    registry = get_registry()
    for entry in registry.all_entries():
        path = f"{entry.endpoint}/.well-known/agent-card.json"
        router.add_api_route(
            path,
            _make_card_handler(entry.agent_id),
            methods=["GET"],
            name=f"a2a_card_{entry.agent_id}",
            tags=["a2a"],
        )
        logger.info(
            "A2A card route bound: agent=%s path=%s",
            entry.agent_id, path,
        )


_register_routes()
