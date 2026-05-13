"""AD-04 output parser — strict JSON parse + Pydantic v2 validation.

The SKILL.md system prompt says: *Return only the JSON object. No
explanation, no markdown, no preamble.* In practice some models still
wrap the payload in a fenced ```json block — this parser strips a single
fence if present and then runs ``json.loads`` once. No silent recovery.

Validation enforces:
* every gap has the required fields (AC-01)
* ``gap_type`` is in ``behaviour.gap_categories`` (AC-03)
* ``severity`` is in ``behaviour.severity_levels`` (AC-04)
* the ``gap_summary`` object is well-shaped (or absent — we recompute it
  authoritatively in ``behaviour.summarise``)

The returned objects are plain dicts so they can be JSON-serialised onto
the HTTP response without further conversion.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.exceptions import OutputParseError


_FENCE_OPEN_PATTERN = re.compile(r"^```(?:json)?[ \t]*\r?\n?", flags=re.IGNORECASE)
_FENCE_CLOSE_PATTERN = re.compile(r"\r?\n?```\s*$")


class _GapItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_id: str = Field(min_length=1)
    req_id_ref: str | None = None
    gap_type: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    description: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    auto_resolvable: bool = False


class _GapPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gap_report: List[_GapItem] = Field(default_factory=list)
    gap_summary: Dict[str, Any] | None = None


def parse(
    raw_text: str,
    *,
    allowed_categories: List[str],
    allowed_severities: List[str],
) -> Dict[str, Any]:
    """Parse and validate an LLM response.

    Returns a dict shaped::

        {
            "gap_report": [ {...}, ... ],
            "gap_summary": {...} | None,
        }

    ``gap_summary`` is left as-is for the caller to recompute via
    ``behaviour.summarise`` — the deterministic counts are the source of
    truth, not whatever the LLM produced.
    """
    payload = _load_json(raw_text)

    try:
        parsed = _GapPayload.model_validate(payload)
    except ValidationError as exc:
        raise OutputParseError(
            "LLM output failed schema validation",
            detail={"errors": exc.errors()},
        ) from exc

    cats = set(allowed_categories)
    sevs = set(allowed_severities)
    for index, item in enumerate(parsed.gap_report):
        if item.gap_type not in cats:
            raise OutputParseError(
                f"Gap #{index + 1} has unknown gap_type '{item.gap_type}'",
                detail={
                    "gap_id": item.gap_id,
                    "gap_type": item.gap_type,
                    "allowed": sorted(cats),
                },
            )
        if item.severity not in sevs:
            raise OutputParseError(
                f"Gap #{index + 1} has unknown severity '{item.severity}'",
                detail={
                    "gap_id": item.gap_id,
                    "severity": item.severity,
                    "allowed": sorted(sevs),
                },
            )

    return {
        "gap_report": [item.model_dump() for item in parsed.gap_report],
        "gap_summary": parsed.gap_summary,
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
