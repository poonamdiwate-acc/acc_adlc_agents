"""PL-05 — FinOps Architect Agent entry point.

Standalone async handler matching the ``AgentHandler`` signature in
:mod:`core.agent_registry`. The handler:

1. Validates the merged payload via :mod:`behaviour.validate_inputs`.
2. Computes deterministic budget allocation (no LLM needed for this).
3. Builds the user message and calls the LLM for PPS thresholds,
   forecast baseline, and project config record generation.
4. Parses + validates the LLM JSON.
5. Merges deterministic budget computation with LLM-generated outputs.
6. Returns the combined activation result dict.

Self-registers on import via ``core.agent_registry.register``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agents.pl05_finops_architect import behaviour, input_builder, output_parser
from core.agent_registry import register
from core.config_loader import get_config
from core.llm_factory import create_llm_client
from core.skill_loader import load_system_prompt

logger = logging.getLogger(__name__)

AGENT_ID = "PL-05"

_config = get_config()
_agent_cfg = _config.agent_config(AGENT_ID)
_behaviour_cfg = _config.behaviour(AGENT_ID)
_inputs_cfg = _config.inputs(AGENT_ID)
_llm_cfg = _config.llm_config(AGENT_ID)
_system_prompt = load_system_prompt(_config.skill_file(AGENT_ID))

_llm_client = create_llm_client(_config)

logger.info(
    "PL-05 initialised: endpoint=%s",
    _agent_cfg.get("agent", {}).get("endpoint"),
)


async def run(payload: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """Execute one FinOps Architect activation run.

    ``payload`` carries the input fields (``project_identity``,
    ``budget_definition``, ``budget_allocation_split``,
    ``cloud_environment``) read from the shared folder.
    """
    behaviour.validate_inputs(payload, _inputs_cfg, _behaviour_cfg)

    # Compute budget allocation deterministically (no LLM needed)
    budget_allocation_plan = behaviour.compute_budget_allocation(
        payload["budget_definition"],
        payload["budget_allocation_split"],
    )

    # Build user message for LLM to generate PPS config, forecast, and project record
    user_message = input_builder.build_user_message(payload)
    logger.info(
        "PL-05 calling LLM: run_id=%s project=%s",
        run_id,
        payload.get("project_identity", {}).get("project_name", "unknown"),
    )

    raw_text = await _llm_client.call(
        system_prompt=_system_prompt,
        user_message=user_message,
        config=_llm_cfg,
        agent_id=AGENT_ID,
    )

    parsed = output_parser.parse(
        raw_text,
        allowed_zones=_behaviour_cfg.get("zone_transitions", []),
        allowed_alert_types=_behaviour_cfg.get("alert_types", []),
    )

    # Post-flight: validate and enrich LLM output deterministically
    pps_threshold_config = behaviour.validate_pps_thresholds(
        parsed.get("pps_threshold_config", {}),
        _behaviour_cfg,
    )

    forecast_baseline = parsed.get("forecast_baseline", {})
    project_config_record = behaviour.build_project_config_record(
        project_identity=payload["project_identity"],
        budget_allocation_plan=budget_allocation_plan,
        pps_threshold_config=pps_threshold_config,
        parsed_config=parsed.get("project_config_record", {}),
    )

    activation_summary = behaviour.build_activation_summary(
        project_identity=payload["project_identity"],
        budget_allocation_plan=budget_allocation_plan,
        pps_threshold_config=pps_threshold_config,
        behaviour_cfg=_behaviour_cfg,
    )

    return {
        "agent_id": AGENT_ID,
        "run_id": run_id,
        "project_config_record": project_config_record,
        "budget_allocation_plan": budget_allocation_plan,
        "pps_threshold_config": pps_threshold_config,
        "forecast_baseline": forecast_baseline,
        "activation_summary": activation_summary,
    }


# Self-register with the agent registry
register(agent_id=AGENT_ID, handler=run, config=_config)
