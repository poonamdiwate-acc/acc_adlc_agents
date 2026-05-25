"""VA-05 — QA Assurance Auditor Agent entry point.

Standalone async handler matching the ``AgentHandler`` signature in
:mod:`core.agent_registry`. The handler:

1. Validates the merged payload via :mod:`behaviour.validate_inputs`
   (FR-1.1, FR-2.1).
2. Runs the deterministic audit-trail review (chronology, duplicates,
   sequence, checkpoint coverage) — these are integrity checks that
   must not depend on the LLM (FR-1.2 / FR-1.3 / FR-1.4 / NFR-2).
3. Builds the user message — including the precomputed findings — and
   calls the LLM for exception disposition and any judgment-based
   audit findings.
4. Parses + validates the LLM JSON.
5. Merges deterministic findings with LLM-supplied judgment findings,
   then enforces FR-2.5 (no Accepted-without-evidence) via
   ``behaviour.reconcile_dispositions``.
6. Recomputes ``assurance_summary`` deterministically — the LLM's own
   summary is never trusted to count its own work.
7. Builds the immutable ``assurance_signoff`` artifact (or ``None`` if
   ``overall_assurance == blocked`` per FR-3.3 / NFR-3).
8. Returns the result dict — the HTTP / MCP layer serialises it and
   the router persists the artifacts to ``qa_assurance/``.

Self-registers on import via ``core.agent_registry.register``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agents.va05_qa_assurance_auditor import behaviour, input_builder, output_parser
from core.agent_registry import register
from core.config_loader import get_config
from core.llm_factory import create_llm_client
from core.skill_loader import load_system_prompt

logger = logging.getLogger(__name__)

AGENT_ID = "VA-05"


_config = get_config()
_agent_cfg = _config.agent_config(AGENT_ID)
_behaviour_cfg = _config.behaviour(AGENT_ID)
_inputs_cfg = _config.inputs(AGENT_ID)
_llm_cfg = _config.llm_config(AGENT_ID)
_system_prompt = load_system_prompt(_config.skill_file(AGENT_ID))

_llm_client = create_llm_client(_config)

logger.info(
    "VA-05 initialised: endpoint=%s",
    _agent_cfg.get("agent", {}).get("endpoint"),
)


async def run(payload: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """Execute one QA Assurance Auditor cycle.

    ``payload`` carries the input fields (``audit_trail``,
    ``exception_flags``, ``project_context``, ``business_case``,
    optional ``checkpoint_expectations``) read from the shared folder
    by the router.
    """
    behaviour.validate_inputs(payload, _inputs_cfg, _behaviour_cfg)
    input_completeness = behaviour.apply_input_defaults(payload, _inputs_cfg)
    if input_completeness["status"] == "partial":
        logger.info(
            "VA-05 partial input: run_id=%s missing=%s degraded=%s",
            run_id,
            input_completeness.get("missing"),
            input_completeness.get("degraded"),
        )

    audit_trail = payload.get("audit_trail") or []
    exception_flags = payload.get("exception_flags") or []
    checkpoint_expectations = payload.get("checkpoint_expectations")

    # --- Deterministic audit-trail review (FR-1.2 / FR-1.3 / FR-1.4) ---
    precomputed_findings = behaviour.review_audit_trail(
        audit_trail=audit_trail,
        checkpoint_expectations=checkpoint_expectations,
    )
    logger.info(
        "VA-05 audit review: run_id=%s entries=%d precomputed_findings=%d",
        run_id, len(audit_trail), len(precomputed_findings),
    )

    # --- LLM call: exception dispositions + judgment-based findings ---
    user_message = input_builder.build_user_message(
        payload, precomputed_findings=precomputed_findings
    )
    logger.info(
        "VA-05 calling LLM: run_id=%s exceptions=%d",
        run_id, len(exception_flags),
    )

    raw_text = await _llm_client.call(
        system_prompt=_system_prompt,
        user_message=user_message,
        config=_llm_cfg,
        agent_id=AGENT_ID,
    )

    parsed = output_parser.parse(
        raw_text,
        allowed_dispositions=_behaviour_cfg.get("disposition_values", []),
        allowed_severities=_behaviour_cfg.get("severity_levels", []),
        allowed_finding_types=_behaviour_cfg.get("audit_finding_types", []),
    )

    # --- Merge deterministic + LLM findings, then renumber ---
    merged_findings = list(precomputed_findings) + list(parsed["audit_findings"])
    audit_findings = behaviour.renumber_findings(merged_findings)

    # --- Enforce FR-2.5 / NFR-2 on dispositions ---
    exception_log = behaviour.reconcile_dispositions(
        exception_log=parsed["exception_log"],
        audit_trail=audit_trail,
        behaviour_cfg=_behaviour_cfg,
    )

    # --- Deterministic assurance summary + sign-off ---
    assurance_summary = behaviour.build_assurance_summary(
        audit_trail=audit_trail,
        exception_log=exception_log,
        audit_findings=audit_findings,
        behaviour_cfg=_behaviour_cfg,
        input_completeness=input_completeness,
    )

    assurance_signoff = behaviour.build_assurance_signoff(
        project_context=payload.get("project_context") or {},
        audit_findings=audit_findings,
        exception_log=exception_log,
        assurance_summary=assurance_summary,
        behaviour_cfg=_behaviour_cfg,
        checkpoint_expectations=checkpoint_expectations,
        input_completeness=input_completeness,
    )

    logger.info(
        "VA-05 complete: run_id=%s overall_assurance=%s signoff_issued=%s",
        run_id,
        assurance_summary["overall_assurance"],
        assurance_signoff is not None,
    )

    return {
        "agent_id": AGENT_ID,
        "run_id": run_id,
        "assurance_signoff": assurance_signoff,
        "exception_log": exception_log,
        "audit_findings": audit_findings,
        "assurance_summary": assurance_summary,
    }


register(agent_id=AGENT_ID, handler=run, config=_config)
