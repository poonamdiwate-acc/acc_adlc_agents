"""DE-04 output parser - strict JSON parse + Pydantic v2 validation.

The SKILL.md system prompt says: *Return only the JSON object. No
explanation, no markdown, no preamble.* In practice some models still
wrap the payload in a fenced ```json block - this parser strips a single
fence if present and then runs ``json.loads`` once. No silent recovery.

Validation enforces:
* every openapi_spec item has the required fields (AC-01)
* ``http_method`` is in ``behaviour.http_methods_allowed`` (AC-09)
* ``contract_category`` is in ``behaviour.contract_categories`` (AC-10)
* ``schema_registry.recommendation`` is in {proceed, review_required,
  blocked} (AC-05) when the LLM returned a registry; the registry is
  authoritatively recomputed by ``behaviour.compute_registry``

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

_ALLOWED_RECOMMENDATIONS = {"proceed", "review_required", "blocked"}


class _OpenAPISpecItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    spec_id: str = Field(min_length=1)
    endpoint_name: str = Field(min_length=1)
    http_method: str = Field(min_length=1)
    path: str = Field(min_length=1)
    description: str = Field(min_length=1)
    contract_category: str = Field(min_length=1)
    request_schema: Optional[Dict[str, Any]] = None
    response_schema: Optional[Dict[str, Any]] = None
    req_id_refs: List[str] = Field(default_factory=list)
    entity_refs: List[str] = Field(default_factory=list)


class _ContractsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openapi_spec: List[_OpenAPISpecItem] = Field(default_factory=list)
    schema_registry: Optional[Dict[str, Any]] = None


def parse(
    raw_text: str,
    *,
    allowed_categories: List[str],
    allowed_methods: List[str],
) -> Dict[str, Any]:
    """Parse and validate an LLM response.

    Returns a dict shaped::

        {
            "openapi_spec":    [ {...}, ... ],
            "schema_registry": {...} | None,
        }

    ``schema_registry`` is left as-is for the caller to recompute via
    ``behaviour.compute_registry`` - the deterministic counts are the
    source of truth, not whatever the LLM produced.
    """
    payload = _load_json(raw_text)

    try:
        parsed = _ContractsPayload.model_validate(payload)
    except ValidationError as exc:
        raise OutputParseError(
            "LLM output failed schema validation",
            detail={"errors": exc.errors()},
        ) from exc

    cats = set(allowed_categories)
    methods = set(allowed_methods)

    for index, item in enumerate(parsed.openapi_spec):
        if item.http_method not in methods:
            raise OutputParseError(
                f"Spec #{index + 1} has unknown http_method '{item.http_method}'",
                detail={
                    "spec_id": item.spec_id,
                    "http_method": item.http_method,
                    "allowed": sorted(methods),
                },
            )
        if item.contract_category not in cats:
            raise OutputParseError(
                f"Spec #{index + 1} has unknown contract_category "
                f"'{item.contract_category}'",
                detail={
                    "spec_id": item.spec_id,
                    "contract_category": item.contract_category,
                    "allowed": sorted(cats),
                },
            )

    registry = _normalize_registry(parsed.schema_registry)

    return {
        "openapi_spec": [item.model_dump() for item in parsed.openapi_spec],
        "schema_registry": registry,
    }


def _normalize_registry(registry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Silently coerce known LLM drift in ``schema_registry`` to safe values.

    The registry is authoritatively recomputed by
    ``behaviour.compute_registry`` downstream, so the LLM's version is
    informational only. Rather than fail the whole parse over a stray
    explanation paragraph or array-vs-int mismatch, normalise the
    offending fields and let the behaviour layer overwrite them.

    * If ``recommendation`` is not in the allowed enum, drop it (set None).
    * If ``uncovered_requirements`` is a list of REQ ids (LLM convention)
      rather than an integer count, replace it with the count and keep
      the list under ``uncovered_requirement_ids`` for traceability.
    """
    if not isinstance(registry, dict):
        return registry

    rec = registry.get("recommendation")
    if rec is not None and rec not in _ALLOWED_RECOMMENDATIONS:
        registry["recommendation"] = None

    uncovered = registry.get("uncovered_requirements")
    if isinstance(uncovered, list):
        registry["uncovered_requirement_ids"] = uncovered
        registry["uncovered_requirements"] = len(uncovered)

    return registry


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
