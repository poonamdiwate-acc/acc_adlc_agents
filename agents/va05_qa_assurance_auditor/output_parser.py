"""VA-05 output parser — strict JSON parse + Pydantic v2 validation.

The SKILL.md system prompt says: *Return only the JSON object. No
explanation, no markdown, no preamble.* In practice some models still
wrap the payload in a fenced ```json block — this parser strips a single
fence if present and then runs ``json.loads`` once.

Validation enforces:
* every exception_log item has the required disposition fields (AC-05, AC-06)
* ``disposition`` is in ``behaviour.disposition_values`` (AC-05)
* ``severity`` is in ``behaviour.severity_levels``
* audit_findings (if any) carry the required shape (AC-07)

assurance_signoff and assurance_summary are NOT trusted from the LLM —
they are rebuilt deterministically in ``behaviour`` from the parsed
exception_log + audit_findings + cycle metadata.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.exceptions import OutputParseError


_FENCE_OPEN_PATTERN = re.compile(r"^```(?:json)?[ \t]*\r?\n?", flags=re.IGNORECASE)
_FENCE_CLOSE_PATTERN = re.compile(r"\r?\n?```\s*$")


class _ExceptionLogItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    exception_id: str = Field(min_length=1)
    source_control: Optional[str] = None
    severity: str = Field(min_length=1)
    disposition: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_refs: List[str] = Field(default_factory=list)
    audit_trail_ref: Optional[str] = None
    reviewed_at: Optional[str] = None
    reviewer: Optional[str] = "VA-05"
    escalation_target: Optional[str] = None


class _AuditFinding(BaseModel):
    model_config = ConfigDict(extra="allow")

    finding_id: Optional[str] = None
    finding_type: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    description: str = Field(min_length=1)
    control_ref: Optional[str] = None
    recommendation: str = Field(min_length=1)


class _AssurancePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    exception_log: List[_ExceptionLogItem] = Field(default_factory=list)
    audit_findings: List[_AuditFinding] = Field(default_factory=list)
    # LLM-suggested summary / signoff are accepted but overwritten by behaviour
    assurance_signoff: Optional[Dict[str, Any]] = None
    assurance_summary: Optional[Dict[str, Any]] = None


def parse(
    raw_text: str,
    *,
    allowed_dispositions: List[str],
    allowed_severities: List[str],
    allowed_finding_types: List[str],
) -> Dict[str, Any]:
    """Parse and validate an LLM response for VA-05.

    Returns a dict shaped::

        {
            "exception_log": [ {...}, ... ],
            "audit_findings": [ {...}, ... ],
        }

    ``assurance_signoff`` and ``assurance_summary`` are deliberately
    dropped here — ``behaviour`` rebuilds them deterministically so a
    buggy LLM cannot mis-route the Validate Orchestrator.
    """
    payload = _load_json(raw_text)

    try:
        parsed = _AssurancePayload.model_validate(payload)
    except ValidationError as exc:
        raise OutputParseError(
            "LLM output failed schema validation",
            detail={"errors": exc.errors()},
        ) from exc

    dispos = set(allowed_dispositions)
    sevs = set(allowed_severities)
    finding_types = set(allowed_finding_types)

    for index, item in enumerate(parsed.exception_log):
        if item.disposition not in dispos:
            raise OutputParseError(
                f"Exception #{index + 1} has unknown disposition '{item.disposition}'",
                detail={
                    "exception_id": item.exception_id,
                    "disposition": item.disposition,
                    "allowed": sorted(dispos),
                },
            )
        if item.severity not in sevs:
            raise OutputParseError(
                f"Exception #{index + 1} has unknown severity '{item.severity}'",
                detail={
                    "exception_id": item.exception_id,
                    "severity": item.severity,
                    "allowed": sorted(sevs),
                },
            )

    for index, finding in enumerate(parsed.audit_findings):
        if finding.finding_type not in finding_types:
            raise OutputParseError(
                f"Audit finding #{index + 1} has unknown finding_type "
                f"'{finding.finding_type}'",
                detail={
                    "finding_type": finding.finding_type,
                    "allowed": sorted(finding_types),
                },
            )
        if finding.severity not in sevs:
            raise OutputParseError(
                f"Audit finding #{index + 1} has unknown severity "
                f"'{finding.severity}'",
                detail={
                    "severity": finding.severity,
                    "allowed": sorted(sevs),
                },
            )

    return {
        "exception_log": [item.model_dump() for item in parsed.exception_log],
        "audit_findings": [f.model_dump() for f in parsed.audit_findings],
    }


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
