"""DE-08 behaviour — config-driven rules around the LLM call.

Three concerns live here:

1. **Pre-flight validation** — does the payload satisfy the config's
   ``inputs`` declarations?
2. **Post-flight totals recomputation** — we never trust LLM arithmetic.
   ``total_monthly_usd`` is the sum of line_items; annual is monthly * 12.
3. **Post-flight confidence/recommendation** — deterministic override based
   on confidence distribution across line items.
4. **ID renumbering** — enforce sequential CE-### and OPT-### IDs.
5. **req_id_refs coercion** — strip hallucinated references.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Set, Tuple

from core.exceptions import PipelineStopError


_RECOMMENDATION_PROCEED = "proceed"
_RECOMMENDATION_OPTIMIZE_FIRST = "optimize_first"
_RECOMMENDATION_BLOCKED = "blocked"


def validate_inputs(
    payload: Dict[str, Any],
    inputs_cfg: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> None:
    """Raise :class:`PipelineStopError` if ``payload`` violates the config."""
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


def renumber_line_items(line_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforce sequential CE-### ids per AC-02."""
    for index, item in enumerate(line_items, start=1):
        item["cost_id"] = f"CE-{index:03d}"
    return line_items


def renumber_optimizations(optimizations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforce sequential OPT-### ids per AC-09."""
    for index, opt in enumerate(optimizations, start=1):
        opt["opt_id"] = f"OPT-{index:03d}"
    return optimizations


def coerce_req_id_refs(
    items: List[Dict[str, Any]],
    structured_requirements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Strip invalid req_id_refs — only keep real REQ-### from input."""
    valid_ids: Set[str] = {
        r.get("req_id") for r in structured_requirements if isinstance(r, dict)
    }
    for item in items:
        refs = item.get("req_id_refs")
        if not isinstance(refs, list):
            item["req_id_refs"] = []
            continue
        item["req_id_refs"] = [ref for ref in refs if ref in valid_ids]
    return items


def recompute_totals(cost_estimate: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute total_monthly_usd and total_annual_usd from line items.

    We never trust LLM arithmetic — the sum of parts is the source of truth.
    """
    line_items = cost_estimate.get("line_items") or []
    total_monthly = sum(
        item.get("monthly_usd", 0) for item in line_items
    )
    cost_estimate["total_monthly_usd"] = round(total_monthly, 2)
    cost_estimate["total_annual_usd"] = round(total_monthly * 12, 2)
    return cost_estimate


def recompute_confidence(
    cost_estimate: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Recompute overall_confidence and recommendation deterministically."""
    line_items = cost_estimate.get("line_items") or []
    blocking_confidence: Set[str] = set(
        behaviour_cfg.get("blocking_confidence", [])
    )

    if not line_items:
        cost_estimate["overall_confidence"] = "low"
        cost_estimate["recommendation"] = _RECOMMENDATION_BLOCKED
        return cost_estimate

    confidence_counts: Counter = Counter()
    has_blocking = False

    for item in line_items:
        confidence = item.get("confidence", "medium")
        confidence_counts[confidence] += 1
        if confidence in blocking_confidence:
            has_blocking = True

    overall, recommendation = _classify_confidence(
        confidence_counts=confidence_counts,
        has_blocking=has_blocking,
    )

    cost_estimate["overall_confidence"] = overall
    cost_estimate["recommendation"] = recommendation
    return cost_estimate


def _classify_confidence(
    confidence_counts: Counter,
    has_blocking: bool,
) -> Tuple[str, str]:
    """Map confidence distribution to overall_confidence + recommendation."""
    total = sum(confidence_counts.values())
    if total == 0:
        return "low", _RECOMMENDATION_BLOCKED

    if has_blocking:
        return "low", _RECOMMENDATION_BLOCKED

    high_count = confidence_counts.get("high", 0)
    if high_count == total:
        return "high", _RECOMMENDATION_PROCEED

    return "medium", _RECOMMENDATION_OPTIMIZE_FIRST
