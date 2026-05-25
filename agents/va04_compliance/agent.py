"""VA-04 - Compliance Agent entry point.

Standalone async handler matching the ``AgentHandler`` signature in
:mod:`core.agent_registry`. The handler:

1. Reads inputs from the shared folder (``build_output/``) containing
   release artefacts and policy rules produced by the Build phase.
   Supports multiple file formats (json, docx, pdf, html).
2. Validates the merged payload via :mod:`behaviour.validate_inputs`.
   Any missing required input raises :class:`PipelineStopError` before
   the LLM is called.
3. Builds the user message and calls the LLM via the SKILL.md system
   prompt (loaded by ``skill_loader``).
4. Parses + validates the LLM JSON via :mod:`output_parser.parse`.
5. Normalises the output:
   - Sequential CA-### check ids (AC-02)
   - Coerced artefact_ref to real artefact ids (AC-03)
   - Coerced policy_ref to real policy rule ids (AC-04)
   - Enforced audit_statuses enum (AC-03)
6. Recomputes ``policy_signoff`` deterministically — we do not trust the
   LLM to count its own output (AC-07).
7. Returns the result dict — the HTTP / MCP layer serialises it.

Self-registers on import via ``core.agent_registry.register``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from agents.va04_compliance import behaviour, input_builder, output_parser
from core.agent_registry import register
from core.config_loader import get_config
from core.llm_factory import create_llm_client
from core.skill_loader import load_system_prompt

logger = logging.getLogger(__name__)

AGENT_ID = "VA-04"

# ---------------------------------------------------------------------------
# Module-level initialisation — runs once on import
# ---------------------------------------------------------------------------

_config        = get_config()
_agent_cfg     = _config.agent_config(AGENT_ID)
_behaviour_cfg = _config.behaviour(AGENT_ID)
_inputs_cfg    = _config.inputs(AGENT_ID)
_llm_cfg       = _config.llm_config(AGENT_ID)
_git_writer_cfg = _agent_cfg.get("git_writer", {})
_system_prompt = load_system_prompt(_config.skill_file(AGENT_ID))

_llm_client = create_llm_client(_config)

logger.info(
    "VA-04 initialised: endpoint=%s  step=%s",
    _agent_cfg.get("agent", {}).get("endpoint"),
    _agent_cfg.get("agent", {}).get("step_number"),
)


def _git_write(result: Dict[str, Any], run_id: str) -> None:
    """Commit the compliance audit trail to the git audit repo.

    Per ``git_writer`` config block:
      write_path_pattern: runs/{run_id}/validate/compliance_audit_trail.json
      commit_msg_pattern: feat(VA-04): Compliance Agent · {run_id}

    Best-effort — any failure is logged as a warning, never raised to caller.
    Git writer is disabled when ADLC_AUDIT_REPO_URL is not a real URL.
    """
    try:
        from gitops.git_reader import GitReader
        repo_url   = _git_writer_cfg.get("repo_url", "")
        git_token  = _git_writer_cfg.get("auth_method", "")
        branch     = _git_writer_cfg.get("branch", "main")
        path_tmpl  = _git_writer_cfg.get("write_path_pattern", "")
        msg_tmpl   = _git_writer_cfg.get("commit_msg_pattern", "feat(VA-04): {run_id}")

        if not repo_url or repo_url.startswith("ENV:") or "your-org" in repo_url:
            logger.info("VA-04 git_writer skipped — ADLC_AUDIT_REPO_URL not configured")
            return

        write_path = path_tmpl.format(run_id=run_id)
        commit_msg = msg_tmpl.format(run_id=run_id)

        content = json.dumps(result, indent=2, ensure_ascii=False)

        writer = GitReader(repo_url=repo_url, token=git_token, branch=branch)
        writer.write_file(path=write_path, content=content, commit_message=commit_msg)

        logger.info("VA-04 git_writer committed: path=%s run_id=%s", write_path, run_id)

    except Exception as exc:
        logger.warning(
            "VA-04 git_writer failed (non-blocking): run_id=%s err=%s",
            run_id, exc,
        )


# ---------------------------------------------------------------------------
# Agent handler
# ---------------------------------------------------------------------------

async def run(payload: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """Execute one Compliance Agent run.

    ``payload`` carries inputs from the shared folder. The shared folder
    merges all extracted fields flat into the payload, so we read
    ``release_artefacts`` and ``policy_rules`` directly from the payload.

    Args:
        payload:  Merged flat dict from all files in ``build_output/``.
        run_id:   Value from the ``X-Run-ID`` request header.

    Returns:
        Dict with ``agent_id``, ``run_id``, ``compliance_audit_trail``,
        and ``policy_signoff``.

    Raises:
        :class:`core.exceptions.PipelineStopError`: on missing required
            inputs or empty artefacts / policy rules.
        :class:`core.exceptions.OutputParseError`: on invalid LLM output.
    """
    # 1. Pre-flight validation — raises PipelineStopError on violation
    behaviour.validate_inputs(payload, _inputs_cfg, _behaviour_cfg)

    # 2. Build LLM user message
    user_message = input_builder.build_user_message(payload)

    release_artefacts = payload.get("release_artefacts") or []
    policy_rules      = payload.get("policy_rules") or []

    logger.info(
        "VA-04 calling LLM: run_id=%s artefacts=%d rules=%d",
        run_id,
        len(release_artefacts),
        len(policy_rules),
    )

    # 3. LLM call
    raw_text = await _llm_client.call(
        system_prompt=_system_prompt,
        user_message=user_message,
        config=_llm_cfg,
        agent_id=AGENT_ID,
    )

    # 4. Parse + schema validation
    audit_statuses = _behaviour_cfg.get("audit_statuses", [
        "compliant",
        "non_compliant",
        "conditionally_compliant",
        "not_applicable",
    ])
    parsed = output_parser.parse(
        raw_text,
        audit_statuses=audit_statuses,
    )

    # 5. Normalise the audit trail
    audit_trail = parsed["compliance_audit_trail"]
    audit_trail = behaviour.enforce_audit_statuses(audit_trail, audit_statuses)
    audit_trail = behaviour.apply_low_confidence_flag(audit_trail, _behaviour_cfg)
    audit_trail = behaviour.coerce_artefact_refs(audit_trail, release_artefacts)
    audit_trail = behaviour.coerce_policy_refs(audit_trail, policy_rules)
    audit_trail = behaviour.renumber_checks(audit_trail)
    audit_trail = behaviour.apply_policy_violation_flag(audit_trail, _behaviour_cfg)

    # 6. Recompute policy_signoff deterministically
    signoff = behaviour.compute_signoff(audit_trail, _behaviour_cfg)

    if behaviour.is_blocking(signoff):
        logger.warning(
            "VA-04 produced blocking signoff: run_id=%s "
            "total_checks=%d non_compliant=%d",
            run_id,
            signoff["total_checks"],
            signoff["non_compliant_count"],
        )

    result = {
        "agent_id":               AGENT_ID,
        "run_id":                 run_id,
        "compliance_audit_trail": audit_trail,
        "policy_signoff":         signoff,
    }

    # 7. Git writer — commit audit trail to audit repo (best-effort, never blocks response)
    _git_write(result, run_id)

    return result


register(agent_id=AGENT_ID, handler=run, config=_config)
