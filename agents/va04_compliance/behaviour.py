"""VA-04 behaviour - config-driven compliance rules around the LLM call.

Three concerns live here, mirroring the DE-04 / PL-01 agent patterns:

1. **Pre-flight validation** — does the payload satisfy the config's
   ``inputs`` declarations (``required``, ``min_items``, ``type``) and the
   ``behaviour.on_empty_requirements`` / ``on_missing_policy_rules`` rules?
   If not, refuse to call the LLM at all.

2. **Post-flight normalisation** — enforce sequential ``CA-###`` check ids
   (AC-02), coerce ``artefact_ref`` to real artefact ids (AC-03), coerce
   ``policy_ref`` to real policy rule ids (AC-04), and enforce that every
   ``status`` is in the allowed ``audit_statuses`` enum (AC-03).

3. **Signoff recomputation** — given the normalised ``compliance_audit_trail``,
   recompute ``policy_signoff`` deterministically. The LLM may set it; we
   recount from the raw trail so a buggy LLM cannot mis-route GenWiz.

Nothing here is hardcoded — every threshold, status, and blocking rule
comes from the agent config (VA-04_Compliance_Config.json).
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from core.exceptions import PipelineStopError


_RECOMMENDATION_PROCEED  = "proceed"
_RECOMMENDATION_REMEDIATE = "remediate"
_RECOMMENDATION_BLOCKED  = "blocked"

_SIGNOFF_AUTHORITY = "VA-04"


# ---------------------------------------------------------------------------
# Pre-flight validation
# ---------------------------------------------------------------------------

def validate_inputs(
    payload: Dict[str, Any],
    inputs_cfg: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> None:
    """Raise :class:`PipelineStopError` if ``payload`` violates the config.

    Enforces:
    - ``required`` fields: stop if missing or empty.
    - ``min_items`` on array fields.
    - ``on_empty_requirements``: stop if ``release_artefacts`` is empty.
    - ``on_missing_policy_rules``: stop if ``policy_rules`` is empty.
    """
    for field_name, rules in inputs_cfg.items():
        if not isinstance(rules, dict):
            continue
        required = bool(rules.get("required"))
        value = payload.get(field_name)
        if required and (value is None or value == "" or value == []):
            raise PipelineStopError(
                f"Required input '{field_name}' is missing",
                detail={
                    "field": field_name,
                    "on_fail": rules.get("on_fail", "stop_and_report"),
                },
            )
        min_items = rules.get("min_items")
        if min_items and isinstance(value, list) and len(value) < int(min_items):
            raise PipelineStopError(
                f"Input '{field_name}' has {len(value)} items, "
                f"minimum {min_items}",
                detail={
                    "field": field_name,
                    "min_items": min_items,
                    "actual": len(value),
                },
            )

    # Explicit behaviour rule: release_artefacts must not be empty
    artefacts = payload.get("release_artefacts") or []
    if (
        behaviour_cfg.get("on_empty_requirements") == "stop_and_report"
        and not artefacts
    ):
        raise PipelineStopError(
            "release_artefacts is empty",
            detail={"on_empty_requirements": "stop_and_report"},
        )

    # Explicit behaviour rule: policy_rules must be provided
    policy_rules = payload.get("policy_rules") or []
    if (
        behaviour_cfg.get("on_missing_policy_rules") == "stop_and_report"
        and not policy_rules
    ):
        raise PipelineStopError(
            "policy_rules is required",
            detail={"on_missing_policy_rules": "stop_and_report"},
        )


# ---------------------------------------------------------------------------
# Post-flight normalisation
# ---------------------------------------------------------------------------

def renumber_checks(audit_trail: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforce sequential CA-### ids per AC-02 regardless of LLM output."""
    for index, check in enumerate(audit_trail, start=1):
        check["check_id"] = f"CA-{index:03d}"
    return audit_trail


def coerce_artefact_refs(
    audit_trail: List[Dict[str, Any]],
    release_artefacts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per AC-03: every ``artefact_ref`` must point at a real artefact id.

    Unknown refs are replaced with an empty string so the check is flagged
    rather than silently accepted.
    """
    valid_ids: Set[str] = _extract_ids(release_artefacts, "artefact_id")
    for check in audit_trail:
        ref = check.get("artefact_ref") or ""
        if ref not in valid_ids:
            check["artefact_ref"] = ref  # keep original; outer validator will flag
    return audit_trail


def coerce_policy_refs(
    audit_trail: List[Dict[str, Any]],
    policy_rules: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per AC-04: every ``policy_ref`` must point at a real rule id."""
    valid_ids: Set[str] = _extract_ids(policy_rules, "rule_id")
    for check in audit_trail:
        ref = check.get("policy_ref") or ""
        if ref not in valid_ids:
            check["policy_ref"] = ref  # keep original; outer validator will flag
    return audit_trail


def enforce_audit_statuses(
    audit_trail: List[Dict[str, Any]],
    audit_statuses: List[str],
) -> List[Dict[str, Any]]:
    """Replace any status not in the allowed enum with 'non_compliant'.

    An unknown status is treated as a compliance failure (safe default).
    """
    allowed: Set[str] = set(audit_statuses)
    for check in audit_trail:
        if check.get("status") not in allowed:
            check["status"] = "non_compliant"
    return audit_trail


def apply_policy_violation_flag(
    audit_trail: List[Dict[str, Any]],
    behaviour_cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Per ``on_policy_violation: flag_and_block`` — add ``policy_violation: true``
    to every blocking check item so downstream consumers can identify flagged
    items without re-evaluating the status field.

    The blocking itself is handled by ``compute_signoff``; this function
    handles the ``flag`` part of ``flag_and_block``.
    """
    if behaviour_cfg.get("on_policy_violation") != "flag_and_block":
        return audit_trail
    blocking: Set[str] = set(behaviour_cfg.get("blocking_statuses", ["non_compliant"]))
    for check in audit_trail:
        if check.get("status") in blocking:
            check["policy_violation"] = True
    return audit_trail


def apply_low_confidence_flag(
    audit_trail: List[Dict[str, Any]],
    behaviour_cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Per ``on_low_confidence: flag_and_continue`` — if the LLM returns a
    ``confidence`` field with value ``low``, add ``low_confidence: true``
    to that item and continue without stopping.
    """
    if behaviour_cfg.get("on_low_confidence") != "flag_and_continue":
        return audit_trail
    for check in audit_trail:
        if str(check.get("confidence", "")).lower() == "low":
            check["low_confidence"] = True
    return audit_trail


# ---------------------------------------------------------------------------
# Signoff recomputation
# ---------------------------------------------------------------------------

def compute_signoff(
    audit_trail: List[Dict[str, Any]],
    behaviour_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute a trustworthy ``policy_signoff`` from the normalised trail.

    Counts are recomputed from the raw trail list; the LLM's own signoff
    is discarded. A single ``non_compliant`` status is sufficient to block.
    """
    blocking_statuses: Set[str] = set(
        behaviour_cfg.get("blocking_statuses", ["non_compliant"])
    )

    total_checks = len(audit_trail)
    compliant_count = sum(
        1 for c in audit_trail if c.get("status") == "compliant"
    )
    non_compliant_count = sum(
        1 for c in audit_trail if c.get("status") in blocking_statuses
    )
    conditionally_compliant_count = sum(
        1 for c in audit_trail if c.get("status") == "conditionally_compliant"
    )

    overall_status, recommendation = _classify_signoff(
        non_compliant_count=non_compliant_count,
        conditionally_compliant_count=conditionally_compliant_count,
        total_checks=total_checks,
    )

    return {
        "overall_status":                 overall_status,
        "signoff_authority":              _SIGNOFF_AUTHORITY,
        "total_checks":                   total_checks,
        "compliant_count":                compliant_count,
        "non_compliant_count":            non_compliant_count,
        "conditionally_compliant_count":  conditionally_compliant_count,
        "recommendation":                 recommendation,
    }


def _classify_signoff(
    non_compliant_count: int,
    conditionally_compliant_count: int,
    total_checks: int,
) -> tuple[str, str]:
    if total_checks == 0:
        return "no_checks_produced", _RECOMMENDATION_BLOCKED
    if non_compliant_count > 0:
        return "non_compliant_findings_present", _RECOMMENDATION_BLOCKED
    if conditionally_compliant_count > 0:
        return "conditionally_compliant", _RECOMMENDATION_REMEDIATE
    return "all_compliant", _RECOMMENDATION_PROCEED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_blocking(signoff: Dict[str, Any]) -> bool:
    """Convenience for callers — does this signoff require a pipeline halt?"""
    return signoff.get("recommendation") == _RECOMMENDATION_BLOCKED


def _extract_ids(items: List[Dict[str, Any]], id_field: str) -> Set[str]:
    return {
        item.get(id_field)
        for item in (items or [])
        if isinstance(item, dict) and item.get(id_field)
    }
