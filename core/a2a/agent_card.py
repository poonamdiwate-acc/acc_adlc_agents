"""Agent Card builder for the A2A (Agent-to-Agent) protocol.

Generates an A2A Agent Card dict from an agent's existing per-agent
config (``<agent>_Config.json``). The agent config is the single source
of truth — there is no hand-maintained card file to drift against.

A2A spec reference (Apr 2025+):
* https://a2aproject.github.io/A2A/
* Discovery URL: ``{agent_url}/.well-known/agent-card.json``

This module is pure data — no HTTP, no FastAPI. The HTTP route layer in
:mod:`api.routers.a2a` handles serving the card.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.config_loader import ADLCConfig, get_config


# Map per-agent inputs/outputs ``type`` values to JSON Schema primitives.
_TYPE_MAP = {
    "string": "string",
    "object": "object",
    "array": "array",
    "boolean": "boolean",
    "integer": "integer",
    "number": "number",
}


def build_agent_card(
    agent_id: str,
    base_url: str,
    *,
    config: Optional[ADLCConfig] = None,
) -> Dict[str, Any]:
    """Return the A2A Agent Card for ``agent_id`` as a plain dict.

    ``base_url`` is the public root URL of the service with no trailing
    slash — e.g. ``http://localhost:8000``. The card's ``url`` is composed
    by appending the agent's REST endpoint from its config.

    The card describes the agent's **logical** inputs/outputs (what it
    operates on), not the transport mechanism (HTTP body vs shared folder
    vs Kafka). That keeps the card stable as transports evolve.
    """
    cfg = config or get_config()
    agent_block = cfg.agent_config(agent_id).get("agent") or {}
    inputs_cfg = cfg.inputs(agent_id)
    outputs_cfg = cfg.outputs(agent_id)
    tech = cfg.tech_stack

    endpoint = agent_block.get("endpoint") or ""
    agent_url = base_url.rstrip("/") + endpoint

    auth_schemes: List[str] = []
    if (tech.get("api") or {}).get("auth") == "bearer_token":
        auth_schemes = ["Bearer"]

    skill: Dict[str, Any] = {
        "id": agent_block.get("mcp_tool_name") or agent_id,
        "name": agent_block.get("name") or agent_id,
        "description": agent_block.get("description") or "",
        "tags": _skill_tags(agent_block),
        "inputModes": ["application/json"],
        "outputModes": ["application/json"],
        "inputSchema": _build_input_schema(inputs_cfg),
        "outputSchema": _build_output_schema(outputs_cfg),
    }

    return {
        "name": agent_block.get("name") or agent_id,
        "description": agent_block.get("description") or "",
        "version": agent_block.get("version") or "0.0.0",
        "url": agent_url,
        "preferredTransport": "HTTP+JSON",
        "capabilities": {
            "streaming": bool((tech.get("llm") or {}).get("stream")),
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "authentication": {"schemes": auth_schemes},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [skill],
    }


def _skill_tags(agent_block: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    phase = agent_block.get("phase")
    if isinstance(phase, str) and phase.strip():
        tags.append(phase)
    if agent_block.get("standalone"):
        tags.append("standalone")
    return tags


def _build_input_schema(inputs_cfg: Dict[str, Any]) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for field_name, spec in inputs_cfg.items():
        if not isinstance(spec, dict):
            continue
        json_type = _TYPE_MAP.get(spec.get("type"), "object")
        properties[field_name] = {"type": json_type}
        if spec.get("required"):
            required.append(field_name)
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _build_output_schema(outputs_cfg: Dict[str, Any]) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    for field_name, spec in outputs_cfg.items():
        if not isinstance(spec, dict):
            continue
        json_type = _TYPE_MAP.get(spec.get("type"), "object")
        properties[field_name] = {"type": json_type}
    return {"type": "object", "properties": properties}
