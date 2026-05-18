"""PL-01 — Gap Detection Agent entry point.

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
from pathlib import Path
from typing import Any, Dict

from agents.pl01_gap_detection import behaviour, input_builder, output_parser
from core.agent_registry import register
from core.config_loader import get_config
from core.llm_client import LLMClient
from core.skill_loader import load_system_prompt
from gitops.git_reader import GitReader, create_git_reader

logger = logging.getLogger(__name__)

AGENT_ID = "PL-01"


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
    "PL-01 initialised: git_reader=%s endpoint=%s",
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

    # Load regulatory checklist if domain+market are available
    regulatory_checklist = _load_regulatory_checklist(resolved)
    if regulatory_checklist:
        resolved["regulatory_checklist"] = regulatory_checklist

    user_message = input_builder.build_user_message(resolved)
    logger.info(
        "PL-01 calling LLM: run_id=%s requirements=%d",
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
                "PL-01 skipping git read: field=%s (already in payload)",
                field_name,
            )
            continue
        git_path = git_path_template.format(run_id=run_id)
        json_field = spec.get("json_field", field_name)
        logger.info(
            "PL-01 reading git input: field=%s path=%s reader=%s",
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


def _load_regulatory_checklist(
    payload: Dict[str, Any],
) -> list | None:
    """Load regulatory checklist based on project_context.domain + market.

    Returns the checklist array if a matching file exists, else None.
    The skill skips gracefully if domain/market are absent or no file found.
    """
    skills_cfg = _agent_cfg.get("skills", {}).get("regulatory_gap_detection", {})
    if not skills_cfg.get("enabled"):
        return None

    project_context = payload.get("project_context") or {}
    domain = project_context.get("domain", "").strip().lower()
    market = project_context.get("market", "").strip().lower()

    if not domain or not market:
        logger.info(
            "PL-01 regulatory_gap_detection: skipping — domain=%r market=%r",
            domain, market,
        )
        return None

    checklist_dir = Path(_config.project_root()) / skills_cfg.get(
        "checklist_dir", "skills/regulatory"
    )
    checklist_file = checklist_dir / f"{domain}_{market}.json"

    if not checklist_file.is_file():
        logger.info(
            "PL-01 regulatory_gap_detection: no checklist at %s — skipping",
            checklist_file,
        )
        return None

    try:
        data = json.loads(checklist_file.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            logger.warning(
                "PL-01 regulatory_gap_detection: checklist is not a list — skipping"
            )
            return None
        logger.info(
            "PL-01 regulatory_gap_detection: loaded %d clauses from %s",
            len(data), checklist_file.name,
        )
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "PL-01 regulatory_gap_detection: failed to load checklist: %s", exc
        )
        return None


register(agent_id=AGENT_ID, handler=run, config=_config)
