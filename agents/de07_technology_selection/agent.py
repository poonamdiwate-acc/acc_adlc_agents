"""DE-07 - Technology Selection Agent entry point.

Standalone async handler matching the ``AgentHandler`` signature in
:mod:`core.agent_registry`. The handler:

1. Reads inputs from the shared folder (``bs_docs`` for requirements documents 
   and ``uploaded_files/brd`` for agent_architecture.json). Supports multiple 
   file formats (json, docx, pdf, html). Requirements are parsed into separate 
   fields: functional_requirements, non_functional_requirements, 
   business_rules_and_constraints, project_context.
2. Validates the merged payload via :mod:`behaviour.validate_inputs`.
3. Builds the user message and calls the LLM.
4. Parses + validates the LLM JSON.
5. Normalises the output: validates exactly 3 stack recommendations with 
   sequential TS-### ids, confidence scores, and recomputes summary.
6. Returns the result dict — the HTTP / MCP layer serialises it.

Self-registers on import via ``core.agent_registry.register``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agents.de07_technology_selection import behaviour, input_builder, output_parser
from core.agent_registry import register
from core.config_loader import get_config
from core.llm_client import LLMClient
from core.skill_loader import load_system_prompt

logger = logging.getLogger(__name__)

AGENT_ID = "DE-07"


_config = get_config()
_agent_cfg = _config.agent_config(AGENT_ID)
_behaviour_cfg = _config.behaviour(AGENT_ID)
_inputs_cfg = _config.inputs(AGENT_ID)
_llm_cfg = _config.llm_config(AGENT_ID)
_system_prompt = load_system_prompt(_config.skill_file(AGENT_ID))

_llm_client = LLMClient()

logger.info(
    "DE-07 initialised: endpoint=%s",
    _agent_cfg.get("agent", {}).get("endpoint"),
)


async def run(payload: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """Execute one Technology Selection run.

    ``payload`` carries inputs from the shared folder. The shared folder
    merges all extracted fields flat into the payload, so we read
    structured_requirements and agent_architecture directly from the payload.
    """
    behaviour.validate_inputs(payload, _inputs_cfg, _behaviour_cfg)

    user_message = input_builder.build_user_message(payload)
    
    # Extract data directly from flat payload structure
    agent_architecture = payload.get("agent_architecture") or {}
    
    # Count total agents (orchestrator + super agents + utility agents)
    total_agents = 0
    if agent_architecture.get("orchestrator_agent"):
        total_agents += 1
    
    super_agents = agent_architecture.get("super_agents", [])
    total_agents += len(super_agents)
    
    # Count utility agents nested in super agents
    for sa in super_agents:
        utility_agents = sa.get("utility_agents", [])
        total_agents += len(utility_agents)
    
    # Count requirements from all parsed fields
    req_count = 0
    for key in ["functional_requirements", "non_functional_requirements", "user_stories"]:
        if key in payload and isinstance(payload[key], list):
            req_count += len(payload[key])
    
    logger.info(
        "DE-07 calling LLM: run_id=%s requirements=%d total_agents=%d (1 orchestrator + %d super agents)",
        run_id, 
        req_count, 
        total_agents,
        len(super_agents),
    )

    raw_text = await _llm_client.call(
        system_prompt=_system_prompt,
        user_message=user_message,
        config=_llm_cfg,
        agent_id=AGENT_ID,
    )

    parsed = output_parser.parse(raw_text)

    result = behaviour.postprocess_output(parsed, _behaviour_cfg)

    logger.info(
        "DE-07 completed: run_id=%s stacks=%d recommended=%s",
        run_id,
        len(result["tech_stack_recommendations"]),
        result["tech_stack_summary"].get("recommended_stack", "unknown"),
    )

    return {
        "agent_id": AGENT_ID,
        "run_id": run_id,
        **result,
    }


register(agent_id=AGENT_ID, handler=run, config=_config)
