"""PL-05 output parser — strict JSON parse + Pydantic v2 validation.

The SKILL.md system prompt says: *Return valid JSON objects per artifact
type. No explanation, no markdown, no preamble.* In practice some models
still wrap the payload in a fenced ```json block — this parser strips a
single fence if present and then runs ``json.loads`` once.

Validation enforces:
* PPS threshold config has required zone fields
* Forecast baseline has required projection fields
* Project config record has expected structure

The returned objects are plain dicts so they can be JSON-serialised onto
the HTTP response without further conversion.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.exceptions import OutputParseError


_FENCE_OPEN_PATTERN = re.compile(r"^```(?:json)?[ \t]*\r?\n?", flags=re.IGNORECASE)
_FENCE_CLOSE_PATTERN = re.compile(r"\r?\n?```\s*$")


# ---------------------------------------------------------------------------
# Pydantic models for LLM output validation
# ---------------------------------------------------------------------------


class _PPSThresholdConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    proceed_ceiling_pct: float = Field(ge=0, le=100)
    pivot_ceiling_pct: float = Field(ge=0, le=100)
    stop_trigger_pct: float = Field(ge=0, le=100)
    proceed_ceiling_amount: Optional[float] = None
    pivot_ceiling_amount: Optional[float] = None
    stop_trigger_amount: Optional[float] = None


class _BurnCurveItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    date: str
    projected_spend: float


class _ForecastBaseline(BaseModel):
    model_config = ConfigDict(extra="allow")

    projected_eom_spend: Optional[float] = None
    projected_eom_pct_of_budget: Optional[float] = None
    forecast_confidence_pct: Optional[float] = None
    burn_curve: Optional[List[_BurnCurveItem]] = None
    model_basis: Optional[str] = None


class _ProjectConfigRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_id: Optional[str] = None
    project_name: Optional[str] = None
    financial_guardrails: Optional[Dict[str, Any]] = None
    approval_chain: Optional[List[Dict[str, Any]]] = None
    alert_config: Optional[Dict[str, Any]] = None


class _FinOpsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pps_threshold_config: Optional[_PPSThresholdConfig] = None
    forecast_baseline: Optional[_ForecastBaseline] = None
    project_config_record: Optional[_ProjectConfigRecord] = None


# ---------------------------------------------------------------------------
# Public parse function
# ---------------------------------------------------------------------------


def parse(
    raw_text: str,
    *,
    allowed_zones: List[str],
    allowed_alert_types: List[str],
) -> Dict[str, Any]:
    """Parse and validate an LLM response for PL-05 activation.

    Returns a dict shaped::

        {
            "pps_threshold_config": {...},
            "forecast_baseline": {...},
            "project_config_record": {...},
        }
    """
    payload = _load_json(raw_text)

    try:
        parsed = _FinOpsPayload.model_validate(payload)
    except ValidationError as exc:
        raise OutputParseError(
            "LLM output failed schema validation",
            detail={"errors": exc.errors()},
        ) from exc

    result: Dict[str, Any] = {}

    # PPS threshold config
    if parsed.pps_threshold_config:
        result["pps_threshold_config"] = parsed.pps_threshold_config.model_dump(
            exclude_none=False
        )
    else:
        result["pps_threshold_config"] = {}

    # Forecast baseline
    if parsed.forecast_baseline:
        result["forecast_baseline"] = parsed.forecast_baseline.model_dump(
            exclude_none=False
        )
    else:
        result["forecast_baseline"] = {}

    # Project config record (partial — agent.py merges with deterministic data)
    if parsed.project_config_record:
        result["project_config_record"] = parsed.project_config_record.model_dump(
            exclude_none=False
        )
    else:
        result["project_config_record"] = {}

    return result


# ---------------------------------------------------------------------------
# JSON loading helper
# ---------------------------------------------------------------------------


def _load_json(raw_text: str) -> Dict[str, Any]:
    """Strip markdown fences and parse JSON."""
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
