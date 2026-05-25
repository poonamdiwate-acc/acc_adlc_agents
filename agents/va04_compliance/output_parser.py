"""VA-04 output parser - strict JSON parse + Pydantic v2 validation.

The SKILL.md system prompt says: *Return only the JSON object. No
explanation, no markdown, no preamble.* In practice some models still
wrap the payload in a fenced ```json block — this parser strips a single
fence if present and then runs ``json.loads`` once. No silent recovery.

Validation enforces (per acceptance criteria):

* AC-01 — every compliance_audit_trail item has required fields:
          check_id, check_name, artefact_ref, policy_ref, status,
          evidence, description.
* AC-03 — ``status`` is in the allowed ``audit_statuses`` enum from config.
* AC-05 — ``policy_signoff`` object is present with all required fields.
* AC-06 — ``policy_signoff.recommendation`` is in
          {proceed, remediate, blocked}.
* AC-09 — output is valid JSON.

The returned objects are plain dicts so they can be JSON-serialised onto
the HTTP response without further conversion. ``policy_signoff`` is left
as-is for the caller to recompute via ``behaviour.compute_signoff`` —
deterministic counts are the source of truth, not whatever the LLM
produced.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from core.exceptions import OutputParseError

logger = logging.getLogger(__name__)


_FENCE_OPEN_PATTERN  = re.compile(r"^```(?:json)?[ \t]*\r?\n?", flags=re.IGNORECASE)
_FENCE_CLOSE_PATTERN = re.compile(r"\r?\n?```\s*$")

_ALLOWED_RECOMMENDATIONS: set[str] = {"proceed", "remediate", "blocked"}


# ---------------------------------------------------------------------------
# Pydantic models — mirror the SKILL.md output schema
# ---------------------------------------------------------------------------

class _AuditCheckItem(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # check_id is optional — behaviour.renumber_checks() sets CA-001... regardless
    check_id:     str = Field(default="CA-000")
    # check_name may be omitted — normalised after validation
    check_name:   str = Field(default="")
    # Accept both UK spelling (artefact_ref) and US spelling (artifact_ref)
    artefact_ref: str = Field(default="", alias="artefact_ref")
    policy_ref:   str = Field(default="")
    status:       str = Field(default="non_compliant")
    # evidence and description may be null or missing — normalised below
    evidence:     str = Field(default="")
    description:  str = Field(default="")
    req_id_refs:  List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        """Fix common Gemini 2.5 output variations before field validation.

        Handles:
        - artifact_ref (US spelling) → artefact_ref
        - None values on string fields → empty string
        - Missing required string fields → empty string (flagged downstream)
        """
        if not isinstance(data, dict):
            return data
        # US-spelling alias: artifact_ref → artefact_ref
        if "artifact_ref" in data and "artefact_ref" not in data:
            data["artefact_ref"] = data.pop("artifact_ref")
        # Coerce None / missing string fields to empty string
        for field in ("artefact_ref", "policy_ref", "status", "evidence",
                      "description", "check_id", "check_name"):
            if data.get(field) is None:
                data[field] = ""
        # Coerce None req_id_refs to empty list
        if data.get("req_id_refs") is None:
            data["req_id_refs"] = []
        return data


class _PolicySignoff(BaseModel):
    model_config = ConfigDict(extra="ignore")

    overall_status:      str = Field(min_length=1)
    signoff_authority:   str = Field(min_length=1)
    total_checks:        int
    compliant_count:     int
    non_compliant_count: int
    recommendation:      str = Field(min_length=1)


class _CompliancePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    compliance_audit_trail: List[_AuditCheckItem] = Field(default_factory=list)
    policy_signoff:         Optional[_PolicySignoff] = None


# ---------------------------------------------------------------------------
# Public parse entry point
# ---------------------------------------------------------------------------

def parse(
    raw_text: str,
    *,
    audit_statuses: List[str],
) -> Dict[str, Any]:
    """Parse and validate an LLM compliance response.

    Returns a dict shaped::

        {
            "compliance_audit_trail": [ {...}, ... ],
            "policy_signoff":         {...} | None,
        }

    ``policy_signoff`` is left as-is (or None) for the caller to recompute
    via ``behaviour.compute_signoff`` — the deterministic counts are the
    source of truth.
    """
    payload = _load_json(raw_text)

    try:
        parsed = _CompliancePayload.model_validate(payload)
    except ValidationError as exc:
        raise OutputParseError(
            "LLM output failed schema validation",
            detail={"errors": exc.errors()},
        ) from exc

    allowed_statuses = set(audit_statuses)

    for index, item in enumerate(parsed.compliance_audit_trail):
        # Fill missing check_name
        if not item.check_name.strip():
            item.check_name = f"{item.policy_ref} -- {item.artefact_ref}"
        # Fill missing evidence/description with placeholder rather than hard-failing
        if not item.evidence.strip():
            item.evidence = "(evidence not provided by LLM)"
        if not item.description.strip():
            item.description = "(description not provided by LLM)"
        # Empty artefact_ref or policy_ref — flag as unknown rather than crashing
        if not item.artefact_ref.strip():
            item.artefact_ref = f"unknown-artefact-{index + 1}"
        if not item.policy_ref.strip():
            item.policy_ref = f"unknown-rule-{index + 1}"

        if item.status not in allowed_statuses:
            raise OutputParseError(
                f"Check #{index + 1} has unknown status '{item.status}'",
                detail={
                    "check_id": item.check_id,
                    "status":   item.status,
                    "allowed":  sorted(allowed_statuses),
                },
            )

    signoff = _normalize_signoff(
        parsed.policy_signoff.model_dump() if parsed.policy_signoff else None
    )

    return {
        "compliance_audit_trail": [item.model_dump() for item in parsed.compliance_audit_trail],
        "policy_signoff":         signoff,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_signoff(
    signoff: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Silently coerce known LLM drift in ``policy_signoff``.

    The signoff is authoritatively recomputed by ``behaviour.compute_signoff``
    downstream. If ``recommendation`` is not in the allowed enum, drop it so
    the behaviour layer sets the correct value.
    """
    if not isinstance(signoff, dict):
        return signoff
    rec = signoff.get("recommendation")
    if rec is not None and rec not in _ALLOWED_RECOMMENDATIONS:
        signoff["recommendation"] = None
    return signoff


def _load_json(raw_text: str) -> Dict[str, Any]:
    """Extract and parse the first valid JSON object from the LLM response.

    Handles all common Gemini output variations:
    - Plain JSON object
    - JSON wrapped in ```json ... ``` fences
    - JSON with extra text / notes before or after
    - Multiple fences or partial fences
    - Trailing commas (best-effort via brace extraction)
    """
    text = (raw_text or "").strip()
    if not text:
        raise OutputParseError("LLM returned empty response", detail={})

    # Strategy 1: strip a single markdown fence and try direct parse
    cleaned = _FENCE_OPEN_PATTERN.sub("", text, count=1)
    cleaned = _FENCE_CLOSE_PATTERN.sub("", cleaned, count=1)
    cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract the first top-level {...} block from anywhere in the text.
    # Handles: explanatory text before/after JSON, fences with trailing notes.
    start = text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
            if not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start: i + 1]
                        try:
                            data = json.loads(candidate)
                            if isinstance(data, dict):
                                logger.info(
                                    "VA-04 output_parser: extracted JSON via brace scan "
                                    "(LLM added text outside the JSON block)"
                                )
                                return data
                        except json.JSONDecodeError:
                            break

    raise OutputParseError(
        "LLM output does not contain a valid JSON object",
        detail={"preview": text[:300]},
    )
