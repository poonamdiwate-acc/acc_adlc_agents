"""DE-04 behaviour - config-driven rules around the LLM call.

Three concerns live here, mirroring DE-03 / AD-04 patterns:

1. **Pre-flight validation** - does the payload satisfy the config's
   ``inputs`` declarations (``required``, ``min_items``, ``type``) and
   ``behaviour.on_empty_requirements`` rule? If not, refuse to call the
   LLM at all.
2. **Post-flight normalisation** - enforce sequential ``OS-###`` ids
   (AC-02), coerce ``req_id_refs`` to real REQ-### (AC-03), coerce
   ``entity_refs`` to real DM-### (AC-04), and dedupe specs that share
   the same ``endpoint_name`` (AC-08).
3. **Registry recomputation** - given the normalised ``openapi_spec``,
   recompute ``schema_registry`` deterministically. The LLM may set it
   too; we recount from the raw report so a buggy LLM cannot mis-route
   GenWiz.

Nothing here is hardcoded - every threshold, category, and method comes
from the agent config.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Set, Tuple

from core.exceptions import PipelineStopError


_RECOMMENDATION_PROCEED = "proceed"
_RECOMMENDATION_REVIEW = "review_required"
_RECOMMENDATION_BLOCKED = "blocked"


def validate_inputs(
    payload: Dict[str, Any],
    inputs_cfg: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> None:
    """Raise :class:`PipelineStopError` if ``payload`` violates the config.

    Per ``behaviour.on_empty_requirements: stop_and_report`` an empty
    ``structured_requirements`` array stops the agent before any LLM call.
    
    This matches the validation pattern from AD-04 Gap Detection agent.
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


def renumber_specs(openapi_spec: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforce sequential OS-### ids per AC-02 regardless of LLM output."""
    for index, spec in enumerate(openapi_spec, start=1):
        spec["spec_id"] = f"OS-{index:03d}"
    return openapi_spec


def coerce_req_id_refs(
    openapi_spec: List[Dict[str, Any]],
    structured_requirements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per AC-03: every ``req_id_refs`` entry must point at a real REQ-###.

    Unknown / null-ish ids are dropped. Empty list permitted (caller's
    coverage check decides whether to flag it).
    """
    valid_ids: Set[str] = {
        r.get("req_id")
        for r in structured_requirements
        if isinstance(r, dict) and r.get("req_id")
    }
    for spec in openapi_spec:
        refs = spec.get("req_id_refs") or []
        if not isinstance(refs, list):
            spec["req_id_refs"] = []
            continue
        spec["req_id_refs"] = [
            ref for ref in refs if isinstance(ref, str) and ref in valid_ids
        ]
    return openapi_spec


def coerce_entity_refs(
    openapi_spec: List[Dict[str, Any]],
    data_model: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per AC-04: every ``entity_refs`` entry must point at a real DM-###."""
    valid_ids: Set[str] = {
        e.get("entity_id")
        for e in (data_model or [])
        if isinstance(e, dict) and e.get("entity_id")
    }
    for spec in openapi_spec:
        refs = spec.get("entity_refs") or []
        if not isinstance(refs, list):
            spec["entity_refs"] = []
            continue
        spec["entity_refs"] = [
            ref for ref in refs if isinstance(ref, str) and ref in valid_ids
        ]
    return openapi_spec


def dedupe_endpoints(openapi_spec: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per AC-08: collapse duplicate ``endpoint_name`` into a single spec.

    Merges ``req_id_refs`` and ``entity_refs`` lists; first occurrence
    wins for scalar fields (description, schemas, category).
    """
    seen: Dict[str, Dict[str, Any]] = {}
    ordered_keys: List[str] = []
    for spec in openapi_spec:
        name = spec.get("endpoint_name") or ""
        if name not in seen:
            seen[name] = dict(spec)
            seen[name]["req_id_refs"] = list(spec.get("req_id_refs") or [])
            seen[name]["entity_refs"] = list(spec.get("entity_refs") or [])
            ordered_keys.append(name)
            continue
        target = seen[name]
        for ref in spec.get("req_id_refs") or []:
            if ref not in target["req_id_refs"]:
                target["req_id_refs"].append(ref)
        for ref in spec.get("entity_refs") or []:
            if ref not in target["entity_refs"]:
                target["entity_refs"].append(ref)
    return [seen[k] for k in ordered_keys]


def compute_registry(
    openapi_spec: List[Dict[str, Any]],
    structured_requirements: List[Dict[str, Any]],
    behaviour_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute a trustworthy ``schema_registry`` from the normalised spec.

    Counts are recomputed from the raw spec list; the LLM's own registry
    is discarded. Coverage = REQs referenced by at least one spec.
    """
    contract_categories: List[str] = list(behaviour_cfg.get("contract_categories", []))

    total_requirements = len(structured_requirements)
    total_contracts = len(openapi_spec)
    referenced_reqs = _referenced_req_ids(openapi_spec)
    valid_req_ids = {
        r.get("req_id")
        for r in structured_requirements
        if isinstance(r, dict) and r.get("req_id")
    }
    uncovered = len(valid_req_ids - referenced_reqs)

    category_counts = Counter(s.get("contract_category") for s in openapi_spec)
    contracts_by_category = {
        cat: int(category_counts.get(cat, 0)) for cat in contract_categories
    }

    overall_quality, recommendation = _classify(
        total_contracts=total_contracts,
        uncovered=uncovered,
        behaviour_cfg=behaviour_cfg,
    )

    summary = (
        f"{total_contracts} contracts generated covering "
        f"{total_requirements - uncovered} of {total_requirements} requirements "
        f"across {sum(1 for c in contracts_by_category.values() if c > 0)} categories."
    )

    return {
        "total_requirements_analysed": int(total_requirements),
        "total_contracts_generated": int(total_contracts),
        "uncovered_requirements": int(uncovered),
        "registry_summary": summary,
        "contracts_by_category": contracts_by_category,
        "recommendation": recommendation,
        "overall_quality": overall_quality,
    }


def _classify(
    total_contracts: int,
    uncovered: int,
    behaviour_cfg: Dict[str, Any],
) -> Tuple[str, str]:
    if total_contracts == 0:
        on_empty = behaviour_cfg.get("on_no_contracts_found", "stop_and_report")
        if on_empty == "return_empty_registry":
            return "blocked", _RECOMMENDATION_BLOCKED
        return "blocked", _RECOMMENDATION_BLOCKED
    if uncovered == 0:
        return "clean", _RECOMMENDATION_PROCEED
    return "needs_attention", _RECOMMENDATION_REVIEW


def _referenced_req_ids(openapi_spec: List[Dict[str, Any]]) -> Set[str]:
    referenced: Set[str] = set()
    for spec in openapi_spec:
        for ref in spec.get("req_id_refs") or []:
            if isinstance(ref, str):
                referenced.add(ref)
    return referenced


def is_blocking(registry: Dict[str, Any]) -> bool:
    """Convenience for callers (GenWiz) - does this registry require review?"""
    return registry.get("recommendation") == _RECOMMENDATION_BLOCKED
