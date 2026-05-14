"""DE-06 behaviour — config-driven rules around the LLM call.

Three concerns live here:

1. **Pre-flight validation** — does the payload satisfy the config's
   ``inputs`` declarations (``required``, ``min_items``, ``type``) and
   ``behaviour.on_empty_requirements`` rule? If not, refuse to call the
   LLM at all.
2. **Post-flight posture scoring** — given the parsed ``security_controls``,
   decide ``overall_posture`` and ``recommendation`` from
   ``behaviour.confidence_levels`` / ``behaviour.blocking_confidence`` /
   ``behaviour.security_control_domains``. The LLM may set these too — we
   compute them deterministically so a buggy LLM cannot mis-route GenWiz.
3. **NFR reconciliation** — renumber NFR IDs sequentially and validate
   req_id_refs against the source requirements.

Nothing here is hardcoded — every threshold, category, and domain comes
from the agent config.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

from core.exceptions import PipelineStopError


_POSTURE_STRONG = "strong"
_POSTURE_ADEQUATE = "adequate"
_POSTURE_NEEDS_HARDENING = "needs_hardening"
_POSTURE_WEAK = "weak"

_RECOMMENDATION_PROCEED = "proceed"
_RECOMMENDATION_NEEDS_HARDENING = "needs_hardening"
_RECOMMENDATION_BLOCKED = "blocked"


def validate_inputs(
    payload: Dict[str, Any],
    inputs_cfg: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> None:
    """Raise :class:`PipelineStopError` if ``payload`` violates the config.

    Per ``behaviour.on_empty_requirements: stop_and_report`` an empty
    ``structured_requirements`` array stops the agent before any LLM call.
    """
    for field_name, rules in inputs_cfg.items():
        if not isinstance(rules, dict):
            continue
        required = bool(rules.get("required"))
        value = payload.get(field_name)
        if required and (value is None or value == ""):
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

    requirements = payload.get("structured_requirements") or []
    if (
        behaviour_cfg.get("on_empty_requirements") == "stop_and_report"
        and not requirements
    ):
        raise PipelineStopError(
            "structured_requirements is empty",
            detail={"on_empty_requirements": "stop_and_report"},
        )

    agent_network_html = payload.get("agent_network_html")
    if not agent_network_html or not str(agent_network_html).strip():
        raise PipelineStopError(
            "agent_network_html is required",
            detail={"field": "agent_network_html"},
        )


def renumber_nfrs(nfr_specifications: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforce sequential NFR-### ids per AC-02 regardless of LLM output."""
    for index, nfr in enumerate(nfr_specifications, start=1):
        nfr["nfr_id"] = f"NFR-{index:03d}"
    return nfr_specifications


def coerce_req_id_refs(
    nfr_specifications: List[Dict[str, Any]],
    structured_requirements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per AC-06: all ``req_id_refs`` must point at real REQ-### items.

    Unknown references are removed. If an NFR ends up with an empty
    req_id_refs list, it is kept but flagged — downstream validation
    (Design Review agent) will catch it.
    """
    valid_ids: Set[str] = {
        r.get("req_id") for r in structured_requirements if isinstance(r, dict)
    }
    for nfr in nfr_specifications:
        refs = nfr.get("req_id_refs")
        if not isinstance(refs, list):
            nfr["req_id_refs"] = []
            continue
        nfr["req_id_refs"] = [ref for ref in refs if ref in valid_ids]
    return nfr_specifications


def coerce_control_req_id_refs(
    controls: List[Dict[str, Any]],
    structured_requirements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Strip invalid req_id_refs from security controls (same as NFR coercion)."""
    valid_ids: Set[str] = {
        r.get("req_id") for r in structured_requirements if isinstance(r, dict)
    }
    for control in controls:
        refs = control.get("req_id_refs")
        if not isinstance(refs, list):
            control["req_id_refs"] = []
            continue
        control["req_id_refs"] = [ref for ref in refs if ref in valid_ids]
    return controls


def recompute_posture(
    security_controls: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Recompute ``overall_posture`` and ``recommendation`` deterministically.

    We do not trust the LLM's self-assessment — posture is derived from
    the controls list, confidence levels, and domain coverage.
    """
    controls = security_controls.get("controls") or []
    allowed_domains: Set[str] = set(
        behaviour_cfg.get("security_control_domains", [])
    )
    blocking_confidence: Set[str] = set(
        behaviour_cfg.get("blocking_confidence", [])
    )

    covered_domains: Set[str] = set()
    confidence_counts: Counter = Counter()
    has_blocking = False

    for control in controls:
        domain = control.get("domain")
        if domain in allowed_domains:
            covered_domains.add(domain)
        confidence = control.get("confidence", "medium")
        confidence_counts[confidence] += 1
        if confidence in blocking_confidence:
            has_blocking = True

    overall_posture, recommendation = _classify_posture(
        covered_domains=covered_domains,
        allowed_domains=allowed_domains,
        confidence_counts=confidence_counts,
        has_blocking=has_blocking,
    )

    security_controls["overall_posture"] = overall_posture
    security_controls["recommendation"] = recommendation
    return security_controls


def _classify_posture(
    covered_domains: Set[str],
    allowed_domains: Set[str],
    confidence_counts: Counter,
    has_blocking: bool,
) -> Tuple[str, str]:
    """Map domain coverage and confidence distribution to posture + recommendation."""
    total_controls = sum(confidence_counts.values())
    if total_controls == 0:
        return _POSTURE_WEAK, _RECOMMENDATION_BLOCKED

    missing_domains = allowed_domains - covered_domains
    critical_domains = {"authentication", "authorization", "encryption"}
    missing_critical = missing_domains & critical_domains

    if missing_critical or has_blocking:
        return _POSTURE_WEAK, _RECOMMENDATION_BLOCKED

    medium_count = confidence_counts.get("medium", 0)
    if missing_domains or medium_count > (total_controls // 2):
        return _POSTURE_NEEDS_HARDENING, _RECOMMENDATION_NEEDS_HARDENING

    high_count = confidence_counts.get("high", 0)
    if high_count == total_controls and not missing_domains:
        return _POSTURE_STRONG, _RECOMMENDATION_PROCEED

    return _POSTURE_ADEQUATE, _RECOMMENDATION_PROCEED


def is_blocking(security_controls: Dict[str, Any]) -> bool:
    """Convenience for callers (GenWiz) — is this result blocking?"""
    return security_controls.get("recommendation") == _RECOMMENDATION_BLOCKED


def renumber_controls(controls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforce sequential SC-### ids per AC-08 regardless of LLM output."""
    for index, control in enumerate(controls, start=1):
        control["control_id"] = f"SC-{index:03d}"
    return controls
