"""Agent Card builder for the A2A (Agent-to-Agent) protocol.

Generates an A2A Agent Card dict from an agent's existing per-agent
config (``<agent>_Config.json``). The agent config is the single source
of truth — there is no hand-maintained card file to drift against.

Targets A2A protocol version 0.2.5. Field names and shapes follow the
canonical schema; optional cosmetic fields (``iconUrl``,
``documentationUrl``, ``provider``) are emitted as ``null`` when we
don't have a value.

A2A spec reference:
* https://a2aproject.github.io/A2A/
* Discovery URL: ``{agent_url}/.well-known/agent-card.json``

This module is pure data — no HTTP, no FastAPI. The HTTP route layer in
:mod:`api.routers.a2a` handles serving the card.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.config_loader import ADLCConfig, get_config


_A2A_PROTOCOL_VERSION = "0.2.5"

# Mirrors ``shared_folder._note`` in ADLC_Tech_Stack_Config:
# "Supports json, docx, pdf, html."
_SHARED_FOLDER_MIME_TYPES: List[str] = [
    "application/json",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/html",
]


def build_agent_card(
    agent_id: str,
    base_url: str,
    *,
    config: Optional[ADLCConfig] = None,
) -> Dict[str, Any]:
    """Return the A2A v0.2.5 Agent Card for ``agent_id`` as a plain dict.

    ``base_url`` is the public root URL of the service with no trailing
    slash — e.g. ``http://localhost:8000``. The card's ``url`` is composed
    by appending the agent's REST endpoint from its config.
    """
    cfg = config or get_config()
    agent_block = cfg.agent_config(agent_id).get("agent") or {}
    tech = cfg.tech_stack

    endpoint = agent_block.get("endpoint") or ""
    agent_url = base_url.rstrip("/") + endpoint

    security_schemes, security = _build_security(tech)
    skill = _build_skill(agent_id, cfg)

    return {
        "protocolVersion": _A2A_PROTOCOL_VERSION,
        "name": agent_block.get("name") or agent_id,
        "description": agent_block.get("description") or "",
        "version": agent_block.get("version") or "0.0.0",
        "url": agent_url,
        "preferredTransport": "HTTP+JSON",
        "additionalInterfaces": None,
        "provider": None,
        "iconUrl": None,
        "documentationUrl": None,
        "capabilities": {
            "streaming": bool((tech.get("llm") or {}).get("stream")),
            "pushNotifications": False,
            "stateTransitionHistory": False,
            "extensions": None,
        },
        "securitySchemes": security_schemes,
        "security": security,
        "defaultInputModes": list(_SHARED_FOLDER_MIME_TYPES),
        "defaultOutputModes": list(_SHARED_FOLDER_MIME_TYPES),
        "skills": [skill],
        "supportsAuthenticatedExtendedCard": False,
    }


def _build_security(
    tech: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[List[Dict[str, List[str]]]]]:
    """Translate tech_stack ``api.auth`` to A2A ``securitySchemes`` + ``security``."""
    auth = (tech.get("api") or {}).get("auth")
    if auth == "bearer_token":
        return (
            {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            [{"bearerAuth": []}],
        )
    return None, None


def _build_skill(agent_id: str, cfg: ADLCConfig) -> Dict[str, Any]:
    agent_block = cfg.agent_config(agent_id).get("agent") or {}
    return {
        "id": agent_block.get("mcp_tool_name") or agent_id,
        "name": agent_block.get("name") or agent_id,
        "description": agent_block.get("description") or "",
        "tags": _skill_tags(agent_block),
        "examples": _skill_examples(agent_id),
        "inputModes": None,
        "outputModes": None,
    }


def _skill_tags(agent_block: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    phase = agent_block.get("phase")
    if isinstance(phase, str) and phase.strip():
        tags.append(phase)
    if agent_block.get("standalone"):
        tags.append("standalone")
    return tags


# Canned examples per agent. Sourced from each agent's SKILL.md test
# cases. Moved into the builder so callers don't have to parse SKILL.md.
_EXAMPLES_BY_AGENT: Dict[str, List[str]] = {
    "PL-01": [
        "Identify gaps in a list of structured requirements against a business case and scope boundaries.",
        "Flag non-functional requirements that lack measurable thresholds (e.g. 'system must be fast' without metrics).",
        "Detect business goals like 'audit logging for SOC2 compliance' that are stated in the business case but not covered by any REQ.",
    ],
}


def _skill_examples(agent_id: str) -> List[str]:
    return list(_EXAMPLES_BY_AGENT.get(agent_id, []))
