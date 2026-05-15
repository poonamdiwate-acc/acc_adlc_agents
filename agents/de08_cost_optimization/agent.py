"""DE-08 — Cost & Optimization Agent entry point.

Standalone async handler matching the ``AgentHandler`` signature in
:mod:`core.agent_registry`. The handler:

1. Resolves any config-declared git-sourced inputs (e.g.
   ``structured_requirements``) via the configured :class:`GitReader`.
2. Validates the merged payload via :mod:`behaviour.validate_inputs`.
3. Builds the user message and calls the LLM.
4. Parses + validates the LLM JSON.
5. Recomputes totals deterministically (never trust LLM arithmetic).
6. Recomputes overall_confidence and recommendation deterministically.
7. Returns the result dict.

Self-registers on import via ``core.agent_registry.register``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from agents.de08_cost_optimization import behaviour, input_builder, output_parser
from core.agent_registry import register
from core.config_loader import get_config
from core.llm_client import LLMClient
from core.skill_loader import load_system_prompt
from gitops.git_reader import GitReader, create_git_reader

logger = logging.getLogger(__name__)

AGENT_ID = "DE-08"


_config = get_config()
_agent_cfg = _config.agent_config(AGENT_ID)
_behaviour_cfg = _config.behaviour(AGENT_ID)
_inputs_cfg = _config.inputs(AGENT_ID)
_llm_cfg = _config.llm_config(AGENT_ID)
_system_prompt = load_system_prompt(_config.skill_file(AGENT_ID))

_llm_client = LLMClient()
_git_reader: GitReader = create_git_reader(
    agent_git_reader_cfg=_config.git_reader_config(AGENT_ID),
    tech_git_reader_cfg=_config.tech_git_reader(),
    project_root=_config.project_root(),
)

logger.info(
    "DE-08 initialised: git_reader=%s endpoint=%s",
    type(_git_reader).__name__,
    _agent_cfg.get("agent", {}).get("endpoint"),
)


async def run(payload: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """Execute one Cost & Optimization run."""
    resolved = _normalize_payload(payload)
    resolved = await _resolve_git_inputs(resolved, run_id)
    behaviour.validate_inputs(resolved, _inputs_cfg, _behaviour_cfg)

    user_message = input_builder.build_user_message(resolved)
    logger.info(
        "DE-08 calling LLM: run_id=%s requirements=%d",
        run_id, len(resolved.get("structured_requirements") or []),
    )

    raw_text = await _llm_client.call(
        system_prompt=_system_prompt,
        user_message=user_message,
        config=_llm_cfg,
        agent_id=AGENT_ID,
    )

    parsed = output_parser.parse(
        raw_text,
        allowed_categories=_behaviour_cfg.get("cost_categories", []),
        allowed_optimization_types=_behaviour_cfg.get("optimization_types", []),
        allowed_priorities=_behaviour_cfg.get("priority_levels", []),
        allowed_confidence=_behaviour_cfg.get("confidence_levels", []),
    )

    requirements = resolved.get("structured_requirements") or []

    cost_estimate = parsed["cost_estimate"]
    cost_estimate["line_items"] = behaviour.renumber_line_items(
        cost_estimate.get("line_items") or []
    )
    cost_estimate["line_items"] = behaviour.coerce_req_id_refs(
        cost_estimate["line_items"],
        requirements,
    )
    cost_estimate = behaviour.recompute_totals(cost_estimate)
    cost_estimate = behaviour.recompute_confidence(cost_estimate, _behaviour_cfg)

    optimization_plan = parsed["optimization_plan"]
    optimization_plan = behaviour.renumber_optimizations(optimization_plan)
    optimization_plan = behaviour.coerce_req_id_refs(optimization_plan, requirements)

    if not cost_estimate.get("line_items") and _behaviour_cfg.get("on_no_estimates_derived") == "return_minimal_estimate":
        logger.warning("DE-08: no cost estimates derived from inputs, returning minimal estimate")

    return {
        "agent_id": AGENT_ID,
        "run_id": run_id,
        "cost_estimate": cost_estimate,
        "optimization_plan": optimization_plan,
    }


_ARCHITECTURE_KEYS = {"orchestrator_agent", "super_agents"}


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Map alternative input shapes to the canonical field names.

    When the shared folder contains an ``agent_architecture.json`` (with keys
    like ``orchestrator_agent`` / ``super_agents``) instead of a literal
    ``agent_network_html`` string, serialise the architecture JSON into the
    ``agent_network_html`` field so the LLM can reason over it.
    """
    normalized = dict(payload)
    if not normalized.get("agent_network_html"):
        arch_data = {
            k: v for k, v in normalized.items()
            if k in _ARCHITECTURE_KEYS and v
        }
        if arch_data:
            normalized["agent_network_html"] = json.dumps(
                arch_data, ensure_ascii=False, indent=2
            )
            logger.info(
                "DE-08 normalized architecture JSON → agent_network_html (%d chars)",
                len(normalized["agent_network_html"]),
            )
    return normalized


async def _resolve_git_inputs(
    payload: Dict[str, Any], run_id: str
) -> Dict[str, Any]:
    """Fetch every input that declares a ``git_path`` and merge into payload.

    Shared-folder values (already in payload) take precedence — if a field
    is already present and non-empty we skip the git read.
    """
    resolved = dict(payload)
    for field_name, spec in _inputs_cfg.items():
        if not isinstance(spec, dict):
            continue
        git_path_template = spec.get("git_path")
        if not git_path_template:
            continue
        if resolved.get(field_name):
            logger.info(
                "DE-08 skipping git read (already in payload): field=%s",
                field_name,
            )
            continue
        git_path = git_path_template.format(run_id=run_id)
        json_field = spec.get("json_field", field_name)
        logger.info(
            "DE-08 reading git input: field=%s path=%s reader=%s",
            field_name, git_path, type(_git_reader).__name__,
        )
        content = await _git_reader.read_json(git_path)
        if not isinstance(content, dict):
            from core.exceptions import GitReadError
            raise GitReadError(
                f"Expected JSON object at {git_path}, got "
                f"{type(content).__name__}",
                detail={"path": git_path, "field": field_name},
            )
        resolved[field_name] = content.get(json_field)
    return resolved


register(agent_id=AGENT_ID, handler=run, config=_config)
