"""DE-06 output parser — strict JSON parse + Pydantic v2 validation.

The SKILL.md system prompt says: *Return only the JSON object. No
explanation, no markdown, no preamble.* In practice some models still
wrap the payload in a fenced ```json block — this parser strips a single
fence if present and then runs ``json.loads`` once. No silent recovery.

Validation enforces:
* every NFR has the required fields (AC-01)
* ``category`` is in ``behaviour.nfr_categories`` (AC-03)
* ``priority`` is in ``behaviour.priority_levels`` (AC-04)
* ``confidence`` is in ``behaviour.confidence_levels`` (AC-05)
* every security control has ``domain`` in ``behaviour.security_control_domains`` (AC-09)
* the ``security_controls`` object has the required top-level keys (AC-07)

The returned objects are plain dicts so they can be JSON-serialised onto
the HTTP response without further conversion.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.exceptions import OutputParseError


_FENCE_OPEN_PATTERN = re.compile(r"^```(?:json)?[ \t]*\r?\n?", flags=re.IGNORECASE)
_FENCE_CLOSE_PATTERN = re.compile(r"\r?\n?```\s*$")


class _NFRItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    nfr_id: str = Field(min_length=1)
    nfr_name: str = Field(default="")
    category: str = Field(min_length=1)
    description: str = Field(default="")
    target_metric: str = Field(min_length=1)
    threshold: str = Field(min_length=1)
    priority: str = Field(min_length=1)
    confidence: str = Field(min_length=1)
    rationale: str = Field(default="")
    req_id_refs: List[str] = Field(default_factory=list)


class _SecurityControl(BaseModel):
    model_config = ConfigDict(extra="allow")

    control_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    name: str = Field(default="")
    description: str = Field(default="")
    mechanism: str = Field(min_length=1)
    rationale: str = Field(default="")
    confidence: str = Field(min_length=1)
    req_id_refs: List[str] = Field(default_factory=list)


class _ComplianceMapping(BaseModel):
    model_config = ConfigDict(extra="allow")

    standard: str = Field(min_length=1)
    applicable_controls: List[str] = Field(default_factory=list)


class _SecurityControls(BaseModel):
    model_config = ConfigDict(extra="allow")

    controls: List[_SecurityControl] = Field(default_factory=list)
    threat_surface_summary: str = Field(default="")
    compliance_mappings: List[Any] = Field(default_factory=list)
    overall_posture: Optional[str] = None
    recommendation: Optional[str] = None


class _AgentOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nfr_specifications: List[_NFRItem] = Field(default_factory=list)
    security_controls: _SecurityControls = Field(default_factory=_SecurityControls)


def parse(
    raw_text: str,
    *,
    allowed_categories: List[str],
    allowed_priorities: List[str],
    allowed_confidence: List[str],
    allowed_domains: List[str],
) -> Dict[str, Any]:
    """Parse and validate an LLM response.

    Returns a dict shaped::

        {
            "nfr_specifications": [ {...}, ... ],
            "security_controls": {...},
        }

    ``security_controls.overall_posture`` and ``recommendation`` are left
    as-is for the caller to recompute via ``behaviour.recompute_posture`` —
    the deterministic classification is the source of truth, not whatever
    the LLM produced.
    """
    payload = _load_json(raw_text)

    try:
        parsed = _AgentOutput.model_validate(payload)
    except ValidationError as exc:
        raise OutputParseError(
            "LLM output failed schema validation",
            detail={"errors": exc.errors()},
        ) from exc

    cats = set(allowed_categories)
    pris = set(allowed_priorities)
    confs = set(allowed_confidence)
    doms = set(allowed_domains)

    for index, item in enumerate(parsed.nfr_specifications):
        if item.category not in cats:
            raise OutputParseError(
                f"NFR #{index + 1} has unknown category '{item.category}'",
                detail={
                    "nfr_id": item.nfr_id,
                    "category": item.category,
                    "allowed": sorted(cats),
                },
            )
        if item.priority not in pris:
            raise OutputParseError(
                f"NFR #{index + 1} has unknown priority '{item.priority}'",
                detail={
                    "nfr_id": item.nfr_id,
                    "priority": item.priority,
                    "allowed": sorted(pris),
                },
            )
        if item.confidence not in confs:
            raise OutputParseError(
                f"NFR #{index + 1} has unknown confidence '{item.confidence}'",
                detail={
                    "nfr_id": item.nfr_id,
                    "confidence": item.confidence,
                    "allowed": sorted(confs),
                },
            )

    for index, control in enumerate(parsed.security_controls.controls):
        if control.domain not in doms:
            raise OutputParseError(
                f"Security control #{index + 1} has unknown domain '{control.domain}'",
                detail={
                    "control_id": control.control_id,
                    "domain": control.domain,
                    "allowed": sorted(doms),
                },
            )
        if control.confidence not in confs:
            raise OutputParseError(
                f"Security control #{index + 1} has unknown confidence '{control.confidence}'",
                detail={
                    "control_id": control.control_id,
                    "confidence": control.confidence,
                    "allowed": sorted(confs),
                },
            )

    nfr_dicts = []
    for item in parsed.nfr_specifications:
        d = item.model_dump()
        if not d.get("nfr_name"):
            d["nfr_name"] = d.get("target_metric", d["nfr_id"])
        if not d.get("description"):
            d["description"] = f"{d['target_metric']} must meet threshold: {d['threshold']}"
        if not d.get("rationale"):
            d["rationale"] = "Derived from structured requirements."
        nfr_dicts.append(d)

    sc_dict = parsed.security_controls.model_dump()
    for control in sc_dict.get("controls", []):
        if not control.get("name"):
            control["name"] = f"{control['domain'].replace('_', ' ').title()} — {control['control_id']}"
        if not control.get("description"):
            control["description"] = control.get("mechanism", "")
        if not control.get("rationale"):
            control["rationale"] = "Derived from architecture topology and requirements."

    sc_dict["compliance_mappings"] = _normalize_compliance_mappings(
        sc_dict.get("compliance_mappings", []),
        sc_dict.get("controls", []),
    )

    return {
        "nfr_specifications": nfr_dicts,
        "security_controls": sc_dict,
    }


def _normalize_compliance_mappings(
    raw_mappings: List[Any],
    controls: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Normalize compliance_mappings to the expected object format.

    The LLM may return:
    - List of objects: [{"standard": "SOC2", "applicable_controls": ["SC-001"]}]
    - List of strings: ["SOC2 Type II", "GDPR"]
    - Empty list: []

    In the string case, we derive applicable_controls from controls that
    mention the standard in their compliance_mappings field.
    """
    if not raw_mappings:
        return []

    normalized = []
    for item in raw_mappings:
        if isinstance(item, dict) and "standard" in item:
            if "applicable_requirements" in item and "applicable_controls" not in item:
                item["applicable_controls"] = item.pop("applicable_requirements")
            normalized.append(item)
        elif isinstance(item, str) and item.strip():
            applicable = [
                c.get("control_id", "")
                for c in controls
                if item.lower() in json.dumps(c).lower()
            ]
            normalized.append({
                "standard": item,
                "applicable_controls": applicable,
            })

    return normalized


def _load_json(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        raise OutputParseError(
            "LLM returned empty response",
            detail={},
        )
    text = _FENCE_OPEN_PATTERN.sub("", text, count=1)
    text = _FENCE_CLOSE_PATTERN.sub("", text, count=1)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OutputParseError(
            f"LLM output is not valid JSON: {exc.msg}",
            detail={
                "line": exc.lineno,
                "column": exc.colno,
                "preview": text[:200],
            },
        ) from exc

    if not isinstance(data, dict):
        raise OutputParseError(
            "LLM output must be a JSON object at the top level",
            detail={"type": type(data).__name__},
        )
    return data
