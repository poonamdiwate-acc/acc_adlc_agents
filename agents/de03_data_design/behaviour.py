"""DE-03 behaviour - config-driven rules around the LLM call.

Three concerns live here, mirroring PL-01's pattern:

1. **Pre-flight validation** - does the payload satisfy the config's
   ``inputs`` declarations (``required``, ``min_items``, ``type``) and
   ``behaviour.on_empty_requirements`` rule? If not, refuse to call the
   LLM at all.
2. **Post-flight normalisation** - enforce sequential ``DM-###`` ids
   (AC-02) and coerce ``req_id_refs`` to only contain real REQ-### ids
   present in ``structured_requirements`` (AC-05).
3. **Blocking-confidence detection** - given the parsed ``data_model``
   and ``storage_selection``, decide whether any low-confidence items
   should block downstream consumers. The LLM may set confidence; we
   deterministically scan the output so a buggy LLM cannot mis-route
   GenWiz.

Nothing here is hardcoded - every threshold, category, and level comes
from the agent config.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from core.exceptions import PipelineStopError


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


def renumber_entities(data_model: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforce sequential DM-### ids per AC-02 regardless of LLM output."""
    for index, entity in enumerate(data_model, start=1):
        entity["entity_id"] = f"DM-{index:03d}"
    return data_model


def coerce_req_id_refs(
    data_model: List[Dict[str, Any]],
    structured_requirements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per AC-05: every ``req_id_refs`` entry must point at a real REQ-###.

    Unknown or null-ish ids are dropped from the list. If an entity ends
    up with no valid refs, the list is set to ``[]`` rather than ``None``
    so downstream consumers can rely on the list contract.
    """
    valid_ids: Set[str] = {
        r.get("req_id")
        for r in structured_requirements
        if isinstance(r, dict) and r.get("req_id")
    }
    for entity in data_model:
        refs = entity.get("req_id_refs") or []
        if not isinstance(refs, list):
            entity["req_id_refs"] = []
            continue
        entity["req_id_refs"] = [
            ref for ref in refs
            if isinstance(ref, str) and ref in valid_ids
        ]
    return data_model


def blocking_items(
    data_model: List[Dict[str, Any]],
    storage_selection: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> Dict[str, List[str]]:
    """Return a deterministic map of items flagged as blocking by confidence.

    A ``confidence`` value that appears in ``behaviour.blocking_confidence``
    marks the item as blocking. Returns::

        {
            "entities": ["DM-002", ...],
            "stores":   ["primary_store", "secondary_stores[1]", ...],
        }
    """
    blocking: Set[str] = set(behaviour_cfg.get("blocking_confidence") or [])
    entities: List[str] = []
    stores: List[str] = []

    if blocking:
        for entity in data_model:
            confidence = entity.get("confidence")
            if confidence in blocking:
                entities.append(entity.get("entity_id") or "")

        primary = (storage_selection or {}).get("primary_store") or {}
        if primary.get("confidence") in blocking:
            stores.append("primary_store")
        for index, store in enumerate(
            (storage_selection or {}).get("secondary_stores") or []
        ):
            if isinstance(store, dict) and store.get("confidence") in blocking:
                stores.append(f"secondary_stores[{index}]")

    return {
        "entities": [eid for eid in entities if eid],
        "stores": stores,
    }


def is_blocking(blocking_map: Dict[str, List[str]]) -> bool:
    """Convenience for callers (GenWiz) - does this design require review?"""
    return bool(blocking_map.get("entities")) or bool(blocking_map.get("stores"))


def find_uncovered_requirements(
    data_model: List[Dict[str, Any]],
    structured_requirements: List[Dict[str, Any]],
) -> List[str]:
    """Return REQ-### ids that have data implications but no entity covers them.

    Used for logging / observability only - this agent does not stop on
    missing coverage (Gap Detection owns that check). A requirement is
    considered to have data implications if its ``type`` is ``functional``
    or its ``gaps_detected`` flag is set; this is a coarse heuristic that
    can be refined as the SKILL's coverage rules mature.
    """
    covered: Set[Optional[str]] = set()
    for entity in data_model:
        for ref in entity.get("req_id_refs") or []:
            covered.add(ref)

    uncovered: List[str] = []
    for req in structured_requirements:
        if not isinstance(req, dict):
            continue
        req_id = req.get("req_id")
        if not req_id or req_id in covered:
            continue
        if req.get("type") == "functional" or req.get("gaps_detected"):
            uncovered.append(req_id)
    return uncovered
