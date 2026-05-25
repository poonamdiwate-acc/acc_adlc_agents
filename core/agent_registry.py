"""Agent registry — the route-to-right-agent core.

Per ``ADLC_Tech_Stack_Config.json`` v2.0.0 ADLC exposes standalone stateless
agents; orchestration lives in GenWiz. This module is the in-process
dispatch table used by both the FastAPI HTTP layer and the MCP tool layer to
look up an agent by ID, endpoint, or MCP tool name and invoke it.

Registration happens once at import time via :func:`register`. Lookup is
read-only at runtime. Every registration must be backed by an entry in
``ADLC_Tech_Stack_Config.json#config_loader.files`` — entries that are not
loaded are rejected so a typo cannot silently route to nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.config_loader import ADLCConfig, get_config
from core.exceptions import ADLCError, ConfigLoadError

logger = logging.getLogger(__name__)

AgentHandler = Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]
"""Async agent entry point. Takes ``(payload, run_id)``, returns a result dict."""


@dataclass(frozen=True)
class AgentEntry:
    """One row in the registry — everything the dispatcher needs."""

    agent_id: str
    name: str
    endpoint: str
    mcp_tool_name: Optional[str]
    handler: AgentHandler


class AgentNotFound(ADLCError):
    """No agent registered for the requested ID / endpoint / MCP tool."""


class _AgentRegistry:
    def __init__(self) -> None:
        self._by_id: Dict[str, AgentEntry] = {}
        self._by_endpoint: Dict[str, AgentEntry] = {}
        self._by_mcp_tool: Dict[str, AgentEntry] = {}

    def register(
        self,
        *,
        agent_id: str,
        handler: AgentHandler,
        config: Optional[ADLCConfig] = None,
    ) -> AgentEntry:
        """Bind ``handler`` to the agent identified by ``agent_id``.

        Endpoint and MCP tool name come from the agent's own config —
        callers cannot override them, which keeps the routing surface a
        single source of truth.
        """
        cfg = config or get_config()
        if agent_id not in cfg.agent_ids:
            raise ConfigLoadError(
                f"Cannot register '{agent_id}' — not in "
                f"config_loader.files map",
                detail={
                    "agent_id": agent_id,
                    "known_agents": cfg.agent_ids,
                },
            )

        agent_block = cfg.agent_config(agent_id).get("agent") or {}
        entry = AgentEntry(
            agent_id=agent_id,
            name=agent_block.get("name", agent_id),
            endpoint=cfg.endpoint(agent_id),
            mcp_tool_name=cfg.mcp_tool_name(agent_id),
            handler=handler,
        )

        if agent_id in self._by_id:
            raise ConfigLoadError(
                f"Agent '{agent_id}' is already registered",
                detail={"agent_id": agent_id},
            )
        if entry.endpoint in self._by_endpoint:
            raise ConfigLoadError(
                f"Endpoint '{entry.endpoint}' collides with "
                f"'{self._by_endpoint[entry.endpoint].agent_id}'",
                detail={"endpoint": entry.endpoint},
            )

        self._by_id[agent_id] = entry
        self._by_endpoint[entry.endpoint] = entry
        if entry.mcp_tool_name:
            if entry.mcp_tool_name in self._by_mcp_tool:
                raise ConfigLoadError(
                    f"MCP tool '{entry.mcp_tool_name}' collides with "
                    f"'{self._by_mcp_tool[entry.mcp_tool_name].agent_id}'",
                    detail={"mcp_tool": entry.mcp_tool_name},
                )
            self._by_mcp_tool[entry.mcp_tool_name] = entry

        logger.info(
            "Agent registered: id=%s endpoint=%s mcp_tool=%s",
            agent_id, entry.endpoint, entry.mcp_tool_name,
        )
        return entry

    def get(self, agent_id: str) -> AgentEntry:
        entry = self._by_id.get(agent_id)
        if entry is None:
            raise AgentNotFound(
                f"No agent registered with id '{agent_id}'",
                detail={
                    "agent_id": agent_id,
                    "known_ids": sorted(self._by_id.keys()),
                },
            )
        return entry

    def get_by_endpoint(self, endpoint: str) -> AgentEntry:
        entry = self._by_endpoint.get(endpoint)
        if entry is None:
            raise AgentNotFound(
                f"No agent registered on endpoint '{endpoint}'",
                detail={
                    "endpoint": endpoint,
                    "known_endpoints": sorted(self._by_endpoint.keys()),
                },
            )
        return entry

    def get_by_mcp_tool(self, tool_name: str) -> AgentEntry:
        entry = self._by_mcp_tool.get(tool_name)
        if entry is None:
            raise AgentNotFound(
                f"No agent registered for MCP tool '{tool_name}'",
                detail={
                    "tool_name": tool_name,
                    "known_tools": sorted(self._by_mcp_tool.keys()),
                },
            )
        return entry

    def all_entries(self) -> List[AgentEntry]:
        return [self._by_id[k] for k in sorted(self._by_id.keys())]

    async def dispatch(
        self,
        agent_id: str,
        payload: Dict[str, Any],
        run_id: str,
    ) -> Dict[str, Any]:
        """Look up ``agent_id`` and ``await`` its handler.

        This is the single chokepoint every caller — HTTP router, MCP tool,
        or future scheduler — should go through, so logging and error shape
        stay uniform.
        """
        entry = self.get(agent_id)
        logger.info(
            "Routing to agent: id=%s run_id=%s endpoint=%s",
            entry.agent_id, run_id, entry.endpoint,
        )
        return await entry.handler(payload, run_id)

    def reset(self) -> None:
        """Clear all registrations — tests only."""
        self._by_id.clear()
        self._by_endpoint.clear()
        self._by_mcp_tool.clear()


_registry = _AgentRegistry()


def register(
    *,
    agent_id: str,
    handler: AgentHandler,
    config: Optional[ADLCConfig] = None,
) -> AgentEntry:
    """Module-level registration shortcut. See :meth:`_AgentRegistry.register`."""
    return _registry.register(agent_id=agent_id, handler=handler, config=config)


def get_registry() -> _AgentRegistry:
    """Return the process-wide registry singleton."""
    return _registry


def bootstrap() -> None:
    """Import every agent module so each one self-registers on import.

    Add new agents here. Import-time side effects are confined to one place
    so callers can be sure the registry is fully populated before they read
    it.
    """
    from agents.pl01_gap_detection import agent as pl01  # noqa: F401
    from agents.pl05_finops_architect import agent as pl05  # noqa: F401
    from agents.de03_data_design import agent as de03  # noqa: F401
    from agents.de04_api_contracts import agent as de04  # noqa: F401
    from agents.va05_qa_assurance_auditor import agent as va05  # noqa: F401
