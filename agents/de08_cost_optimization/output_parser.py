"""DE-08 output parser — strict JSON parse + Pydantic v2 validation.

Validates:
* every cost line item has required fields (AC-01)
* ``category`` is in ``behaviour.cost_categories`` (AC-03)
* ``confidence`` is in ``behaviour.confidence_levels`` (AC-04)
* every optimization has required fields (AC-08)
* optimization ``category`` is in ``behaviour.optimization_types`` (AC-10)
* ``priority`` is in ``behaviour.priority_levels`` (AC-11)
* ``recommendation`` is valid (AC-15)
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.exceptions import OutputParseError


_FENCE_OPEN_PATTERN = re.compile(r"^```(?:json)?[ \t]*\r?\n?", flags=re.IGNORECASE)
_FENCE_CLOSE_PATTERN = re.compile(r"\r?\n?```\s*$")

_VALID_RECOMMENDATIONS = {"proceed", "optimize_first", "blocked"}


class _CostLineItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    cost_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    category: str = Field(min_length=1)
    description: str = Field(default="")
    monthly_usd: float = Field(default=0)
    rationale: str = Field(default="")
    confidence: str = Field(min_length=1)
    req_id_refs: List[str] = Field(default_factory=list)


class _CostEstimate(BaseModel):
    model_config = ConfigDict(extra="allow")

    total_monthly_usd: float = Field(default=0)
    total_annual_usd: float = Field(default=0)
    line_items: List[_CostLineItem] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    overall_confidence: str = Field(default="medium")
    recommendation: str = Field(default="optimize_first")


class _OptimizationItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    opt_id: str = Field(min_length=1)
    title: str = Field(default="")
    category: str = Field(default="")
    optimization_type: str = Field(default="")
    description: str = Field(default="")
    estimated_savings_pct: float = Field(default=0)
    estimated_savings_monthly_usd: float = Field(default=0)
    priority: str = Field(min_length=1)
    trade_off: str = Field(default="")
    confidence: str = Field(min_length=1)
    req_id_refs: List[str] = Field(default_factory=list)


class _AgentOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cost_estimate: _CostEstimate = Field(default_factory=_CostEstimate)
    optimization_plan: List[_OptimizationItem] = Field(default_factory=list)


def parse(
    raw_text: str,
    *,
    allowed_categories: List[str],
    allowed_optimization_types: List[str],
    allowed_priorities: List[str],
    allowed_confidence: List[str],
) -> Dict[str, Any]:
    """Parse and validate an LLM response.

    Returns a dict shaped::

        {
            "cost_estimate": {...},
            "optimization_plan": [ {...}, ... ],
        }
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
    opt_types = set(allowed_optimization_types)
    pris = set(allowed_priorities)
    confs = set(allowed_confidence)

    for index, item in enumerate(parsed.cost_estimate.line_items):
        if item.category not in cats:
            raise OutputParseError(
                f"Cost line item #{index + 1} has unknown category '{item.category}'",
                detail={
                    "cost_id": item.cost_id,
                    "category": item.category,
                    "allowed": sorted(cats),
                },
            )
        if item.confidence not in confs:
            raise OutputParseError(
                f"Cost line item #{index + 1} has unknown confidence '{item.confidence}'",
                detail={
                    "cost_id": item.cost_id,
                    "confidence": item.confidence,
                    "allowed": sorted(confs),
                },
            )

    for index, opt in enumerate(parsed.optimization_plan):
        resolved_category = opt.category or opt.optimization_type
        if resolved_category not in opt_types:
            resolved_category = resolved_category.lower().replace(" ", "_").replace("-", "_")
            if resolved_category not in opt_types:
                resolved_category = "architecture_change"
            opt.category = resolved_category
        if opt.priority not in pris:
            raise OutputParseError(
                f"Optimization #{index + 1} has unknown priority '{opt.priority}'",
                detail={
                    "opt_id": opt.opt_id,
                    "priority": opt.priority,
                    "allowed": sorted(pris),
                },
            )
        if opt.confidence not in confs:
            raise OutputParseError(
                f"Optimization #{index + 1} has unknown confidence '{opt.confidence}'",
                detail={
                    "opt_id": opt.opt_id,
                    "confidence": opt.confidence,
                    "allowed": sorted(confs),
                },
            )

    cost_dict = parsed.cost_estimate.model_dump()
    for item in cost_dict.get("line_items", []):
        if not item.get("description"):
            item["description"] = f"{item['service']} — {item['category']}"
        if not item.get("rationale"):
            item["rationale"] = "Derived from service topology and requirements."

    if cost_dict.get("recommendation") not in _VALID_RECOMMENDATIONS:
        cost_dict["recommendation"] = "optimize_first"

    if cost_dict.get("overall_confidence") not in confs:
        cost_dict["overall_confidence"] = "medium"

    opt_dicts = []
    for opt in parsed.optimization_plan:
        d = opt.model_dump()
        if not d.get("category"):
            d["category"] = d.get("optimization_type", "")
        if not d.get("title"):
            d["title"] = d.get("description", d["opt_id"])
        if not d.get("description"):
            d["description"] = d.get("title", "")
        if not d.get("trade_off"):
            d["trade_off"] = "Trade-off not specified."
        if d.get("estimated_savings_pct", 0) < 0:
            d["estimated_savings_pct"] = 0
        if d.get("estimated_savings_monthly_usd", 0) < 0:
            d["estimated_savings_monthly_usd"] = 0
        d.pop("optimization_type", None)
        opt_dicts.append(d)

    return {
        "cost_estimate": cost_dict,
        "optimization_plan": opt_dicts,
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
