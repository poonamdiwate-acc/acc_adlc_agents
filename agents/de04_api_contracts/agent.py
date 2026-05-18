"""DE-04 - Api Contracts Agent entry point.

Standalone async handler matching the ``AgentHandler`` signature in
:mod:`core.agent_registry`. The handler:

1. Reads inputs from the shared folder (``bs_docs`` and 
   ``data_design_response``) containing business requirements and data
   design model. Supports multiple file formats (json, docx, pdf, html).
2. Validates the merged payload via :mod:`behaviour.validate_inputs`.
3. Builds the user message and calls the LLM.
4. Parses + validates the LLM JSON.
5. Normalises the output: sequential OS-### ids, coerced ref lists,
   deduplicated endpoint names.
6. Recomputes ``schema_registry`` deterministically (we do not trust the
   LLM to count its own output).
7. Returns the result dict - the HTTP / MCP layer serialises it.

Self-registers on import via ``core.agent_registry.register``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agents.de04_api_contracts import behaviour, input_builder, output_parser
from core.agent_registry import register
from core.config_loader import get_config
from core.llm_client import LLMClient
from core.skill_loader import load_system_prompt

logger = logging.getLogger(__name__)

AGENT_ID = "DE-04"


_config = get_config()
_agent_cfg = _config.agent_config(AGENT_ID)
_behaviour_cfg = _config.behaviour(AGENT_ID)
_inputs_cfg = _config.inputs(AGENT_ID)
_llm_cfg = _config.llm_config(AGENT_ID)
_system_prompt = load_system_prompt(_config.skill_file(AGENT_ID))

_llm_client = LLMClient()

logger.info(
    "DE-04 initialised: endpoint=%s",
    _agent_cfg.get("agent", {}).get("endpoint"),
)


async def run(payload: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """Execute one Api Contracts run.

    ``payload`` carries inputs from the shared folder. The shared folder
    merges all extracted fields flat into the payload, so we read
    structured_requirements and data_model directly from the payload.
    """
    behaviour.validate_inputs(payload, _inputs_cfg, _behaviour_cfg)

    user_message = input_builder.build_user_message(payload)
    
    # Extract data directly from flat payload structure
    structured_requirements = payload.get("structured_requirements") or []
    data_model = (
        payload.get("data_model") or 
        payload.get("data_design_model_and_strategy") or 
        []
    )
    
    logger.info(
        "DE-04 calling LLM: run_id=%s requirements=%d entities=%d",
        run_id, len(structured_requirements), len(data_model),
    )

    raw_text = await _llm_client.call(
        system_prompt=_system_prompt,
        user_message=user_message,
        config=_llm_cfg,
        agent_id=AGENT_ID,
    )

    parsed = output_parser.parse(
        raw_text,
        allowed_categories=_behaviour_cfg.get("contract_categories", []),
        allowed_methods=_behaviour_cfg.get("http_methods_allowed", []),
    )

    openapi_spec = behaviour.coerce_req_id_refs(
        parsed["openapi_spec"], structured_requirements,
    )
    openapi_spec = behaviour.coerce_entity_refs(openapi_spec, data_model)
    openapi_spec = behaviour.dedupe_endpoints(openapi_spec)
    openapi_spec = behaviour.renumber_specs(openapi_spec)

    schema_registry = behaviour.compute_registry(
        openapi_spec=openapi_spec,
        structured_requirements=structured_requirements,
        behaviour_cfg=_behaviour_cfg,
    )

    if behaviour.is_blocking(schema_registry):
        logger.warning(
            "DE-04 produced blocking registry: run_id=%s contracts=%d uncovered=%d",
            run_id,
            schema_registry["total_contracts_generated"],
            schema_registry["uncovered_requirements"],
        )

    return {
        "agent_id": AGENT_ID,
        "run_id": run_id,
        "openapi_spec": openapi_spec,
        "schema_registry": schema_registry,
    }


register(agent_id=AGENT_ID, handler=run, config=_config)
