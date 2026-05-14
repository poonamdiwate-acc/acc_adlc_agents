"""AD-04 — Gap Detection Agent entry point.

Standalone async handler matching the ``AgentHandler`` signature in
:mod:`core.agent_registry`. The handler:

1. Resolves any config-declared git-sourced inputs (e.g.
   ``structured_requirements``) via the configured :class:`GitReader` —
   local file in dev, real git in prod, picked per
   ``<agent>_Config.json#git_reader.enabled``.
2. Validates the merged payload via :mod:`behaviour.validate_inputs`.
3. Builds the user message and calls the LLM.
4. Parses + validates the LLM JSON.
5. Recomputes ``gap_summary`` deterministically (we do not trust the LLM
   to count its own output).
6. Returns the result dict — the HTTP / MCP layer serialises it.

Self-registers on import via ``core.agent_registry.register``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from agents.ad04_gap_detection import behaviour, input_builder, output_parser
from core.agent_registry import register
from core.config_loader import get_config
from core.llm_client import LLMClient
from core.skill_loader import load_system_prompt
from gitops.git_reader import GitReader, create_git_reader

logger = logging.getLogger(__name__)

AGENT_ID = "AD-04"


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
    "AD-04 initialised: git_reader=%s endpoint=%s",
    type(_git_reader).__name__,
    _agent_cfg.get("agent", {}).get("endpoint"),
)


async def run(payload: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """Execute one Gap Detection run.

    ``payload`` carries the phase_input fields (``business_case``,
    ``project_context``, optional ``scope_boundaries``). Git-sourced
    fields (e.g. ``structured_requirements``) are fetched here using
    ``run_id`` against the configured :class:`GitReader`.
    """
    resolved = await _resolve_git_inputs(payload, run_id)
    behaviour.validate_inputs(resolved, _inputs_cfg, _behaviour_cfg)

    user_message = input_builder.build_user_message(resolved)
    logger.info(
        "AD-04 calling LLM: run_id=%s requirements=%d",
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
        allowed_categories=_behaviour_cfg.get("gap_categories", []),
        allowed_severities=_behaviour_cfg.get("severity_levels", []),
    )

    gap_report = behaviour.coerce_req_id_refs(
        parsed["gap_report"],
        resolved.get("structured_requirements") or [],
    )
    gap_report = behaviour.renumber_gaps(gap_report)

    gap_summary = behaviour.summarise(
        gap_report,
        total_requirements=len(resolved.get("structured_requirements") or []),
        behaviour_cfg=_behaviour_cfg,
    )

    return {
        "agent_id": AGENT_ID,
        "run_id": run_id,
        "gap_report": gap_report,
        "gap_summary": gap_summary,
    }


async def _resolve_git_inputs(
    payload: Dict[str, Any], run_id: str
) -> Dict[str, Any]:
    """Fetch every input that declares a ``git_path`` and merge into payload.

    If a field is already present in the payload (e.g. read from the shared
    folder), the git read is skipped — shared folder takes precedence.
    """
    resolved = dict(payload)
    for field_name, spec in _inputs_cfg.items():
        if not isinstance(spec, dict):
            continue
        git_path_template = spec.get("git_path")
        if not git_path_template:
            continue
        # Skip if already provided (e.g. from shared folder)
        if resolved.get(field_name):
            logger.info(
                "AD-04 skipping git read: field=%s (already in payload)",
                field_name,
            )
            continue
        git_path = git_path_template.format(run_id=run_id)
        json_field = spec.get("json_field", field_name)
        logger.info(
            "AD-04 reading git input: field=%s path=%s reader=%s",
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
