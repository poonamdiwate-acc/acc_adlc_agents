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
import os
from pathlib import Path
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
    resolved = await _resolve_git_inputs(payload, run_id)
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


async def _resolve_git_inputs(
    payload: Dict[str, Any], run_id: str
) -> Dict[str, Any]:
    """Fetch every input that declares a ``git_path`` and merge into payload."""
    resolved = dict(payload)
    dev_overrides = _dev_git_fixture_overrides()
    for field_name, spec in _inputs_cfg.items():
        if not isinstance(spec, dict):
            continue
        git_path_template = spec.get("git_path")
        if not git_path_template:
            continue
        git_path = git_path_template.format(run_id=run_id)
        json_field = spec.get("json_field", field_name)
        if field_name in dev_overrides:
            fixture_path = dev_overrides[field_name]
            logger.info(
                "DE-08 reading dev fixture (override): field=%s path=%s",
                field_name, fixture_path,
            )
            content = _read_dev_fixture(fixture_path, field_name)
        else:
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


def _dev_git_fixture_overrides() -> Dict[str, Path]:
    """Return ``{field_name: absolute fixture path}`` for the current run."""
    if os.environ.get("ENV", "").lower() != "dev":
        return {}
    dev_block = _config.dev_config(AGENT_ID)
    if not dev_block.get("enabled"):
        return {}
    raw = dev_block.get("git_input_fixtures") or {}
    if not isinstance(raw, dict):
        return {}
    project_root = _config.project_root()
    overrides: Dict[str, Path] = {}
    for field, rel_path in raw.items():
        if isinstance(rel_path, str) and rel_path.strip():
            overrides[field] = (project_root / rel_path).resolve()
    return overrides


def _read_dev_fixture(path: Path, field_name: str) -> Dict[str, Any]:
    """Read and JSON-parse a dev git_input_fixture file."""
    from core.exceptions import GitReadError
    if not path.is_file():
        raise GitReadError(
            f"Dev fixture not found for '{field_name}': {path}",
            detail={"path": str(path), "field": field_name},
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GitReadError(
            f"Dev fixture for '{field_name}' is unreadable: {exc}",
            detail={"path": str(path), "field": field_name},
        ) from exc


register(agent_id=AGENT_ID, handler=run, config=_config)
