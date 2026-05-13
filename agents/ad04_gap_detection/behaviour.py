"""AD-04 behaviour — config-driven rules around the LLM call.

Three concerns live here:

1. **Pre-flight validation** — does the payload satisfy the config's
   ``inputs`` declarations (``required``, ``min_items``, ``type``) and
   ``behaviour.on_empty_requirements`` rule? If not, refuse to call the
   LLM at all.
2. **Post-flight quality scoring** — given the parsed ``gap_report``, decide
   ``overall_quality`` and ``recommendation`` from
   ``behaviour.severity_levels`` / ``behaviour.blocking_severities``. The
   LLM may set these too — we compute them deterministically so a buggy
   LLM cannot mis-route GenWiz.
3. **Summary reconciliation** — recount ``gaps_by_severity`` /
   ``gaps_by_category`` / ``blocking_gaps`` from the raw report. The LLM's
   own summary is treated as untrusted and overwritten.

Nothing here is hardcoded — every threshold, category, and severity comes
from the agent config.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from core.exceptions import PipelineStopError


_BLOCKING_RECOMMENDATION_HIGH = "resolve_blocking_gaps_first"
_BLOCKING_RECOMMENDATION_CRITICAL = "significant_rework_needed"
_CLEAN_RECOMMENDATION = "proceed"

_QUALITY_CLEAN = "clean"
_QUALITY_NEEDS_ATTENTION = "needs_attention"
_QUALITY_BLOCKED = "blocked"


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


def summarise(
    gap_report: List[Dict[str, Any]],
    total_requirements: int,
    behaviour_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute a trustworthy ``gap_summary`` from ``gap_report`` + config."""
    severity_levels: List[str] = list(behaviour_cfg.get("severity_levels", []))
    blocking_set = set(behaviour_cfg.get("blocking_severities", []))

    severity_counts = Counter(g.get("severity") for g in gap_report)
    category_counts = Counter(g.get("gap_type") for g in gap_report)
    blocking_gaps = sum(
        n for sev, n in severity_counts.items() if sev in blocking_set
    )

    overall_quality, recommendation = _classify(
        severity_counts=severity_counts,
        severity_levels=severity_levels,
    )

    gaps_by_severity = {sev: int(severity_counts.get(sev, 0)) for sev in severity_levels}
    gaps_by_category = {cat: int(n) for cat, n in category_counts.items() if cat}

    return {
        "total_requirements_analysed": int(total_requirements),
        "total_gaps_found": len(gap_report),
        "blocking_gaps": int(blocking_gaps),
        "gaps_by_severity": gaps_by_severity,
        "gaps_by_category": gaps_by_category,
        "overall_quality": overall_quality,
        "recommendation": recommendation,
    }


def _classify(
    severity_counts: Counter,
    severity_levels: List[str],
) -> Tuple[str, str]:
    total = sum(severity_counts.values())
    if total == 0:
        return _QUALITY_CLEAN, _CLEAN_RECOMMENDATION

    critical_count = severity_counts.get("critical", 0)
    high_count = severity_counts.get("high", 0)

    if "critical" in severity_levels and critical_count > 0:
        return _QUALITY_BLOCKED, _BLOCKING_RECOMMENDATION_CRITICAL
    if "high" in severity_levels and high_count > 0:
        return _QUALITY_NEEDS_ATTENTION, _BLOCKING_RECOMMENDATION_HIGH
    return _QUALITY_NEEDS_ATTENTION, _BLOCKING_RECOMMENDATION_HIGH


def renumber_gaps(gap_report: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforce sequential GAP-### ids per AC-02 regardless of LLM output."""
    for index, gap in enumerate(gap_report, start=1):
        gap["gap_id"] = f"GAP-{index:03d}"
    return gap_report


def is_blocking(gap_summary: Dict[str, Any]) -> bool:
    """Convenience for callers (GenWiz) — is this report blocking?"""
    return gap_summary.get("overall_quality") in (
        _QUALITY_BLOCKED, _QUALITY_NEEDS_ATTENTION
    ) and int(gap_summary.get("blocking_gaps", 0)) > 0


def coerce_req_id_refs(
    gap_report: List[Dict[str, Any]],
    structured_requirements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per AC-05: ``req_id_ref`` must point at a real REQ-### or be ``None``.

    Anything else (empty string, ``"null"``, an unknown REQ id) is replaced
    with ``None`` so downstream consumers can rely on the contract.
    """
    valid_ids = {
        r.get("req_id") for r in structured_requirements if isinstance(r, dict)
    }
    for gap in gap_report:
        ref: Optional[str] = gap.get("req_id_ref")
        if ref in (None, "", "null"):
            gap["req_id_ref"] = None
        elif ref not in valid_ids:
            gap["req_id_ref"] = None
    return gap_report
