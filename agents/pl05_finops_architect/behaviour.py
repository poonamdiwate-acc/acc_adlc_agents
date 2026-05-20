"""PL-05 behaviour — config-driven rules around the LLM call.

Three concerns live here:

1. **Pre-flight validation** — does the payload satisfy the config's
   ``inputs`` declarations (required fields, allocation sum, PPS order)?
   If not, refuse to call the LLM at all.
2. **Deterministic budget computation** — budget allocation plan is
   computed from raw numbers, not by the LLM.
3. **Post-flight validation** — validate PPS thresholds order, build
   the project config record, and generate the activation summary.

Nothing here is hardcoded — every threshold and default comes from the
agent config's ``behaviour`` block.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.exceptions import PipelineStopError


# ---------------------------------------------------------------------------
# Pre-flight input validation
# ---------------------------------------------------------------------------


def validate_inputs(
    payload: Dict[str, Any],
    inputs_cfg: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> None:
    """Raise :class:`PipelineStopError` if ``payload`` violates the config.

    Checks:
    - All required fields present
    - Budget allocation split sums to 100
    - Reserve buffer meets minimum
    """
    # Check required fields per inputs config
    for field_name, rules in inputs_cfg.items():
        if not isinstance(rules, dict):
            continue
        required = bool(rules.get("required"))
        value = payload.get(field_name)

        if required and (value is None or value == "" or value == {}):
            on_fail = rules.get("on_fail", "stop_and_report")
            raise PipelineStopError(
                f"Required input '{field_name}' is missing",
                detail={"field": field_name, "on_fail": on_fail},
            )

        # Validate expected_fields if specified
        expected_fields = rules.get("expected_fields", [])
        if required and expected_fields and isinstance(value, dict):
            missing = [f for f in expected_fields if f not in value or value[f] is None]
            if missing:
                raise PipelineStopError(
                    f"Input '{field_name}' is missing required sub-fields: {missing}",
                    detail={
                        "field": field_name,
                        "missing_sub_fields": missing,
                        "on_fail": rules.get("on_fail", "stop_and_report"),
                    },
                )

    # Validate budget_allocation_split sums to 100
    allocation = payload.get("budget_allocation_split", {})
    if allocation:
        _validate_allocation_sum(allocation)
        _validate_reserve_buffer(allocation, behaviour_cfg)


def _validate_allocation_sum(allocation: Dict[str, Any]) -> None:
    """Ensure all pct fields sum to exactly 100."""
    pct_fields = [
        "compute_pct", "storage_pct", "network_pct",
        "managed_services_pct", "reserve_buffer_pct",
    ]
    total = sum(float(allocation.get(f, 0)) for f in pct_fields)
    if not math.isclose(total, 100.0, abs_tol=0.01):
        raise PipelineStopError(
            "budget_allocation_split does not sum to 100",
            detail={
                "field": "budget_allocation_split",
                "actual_sum": total,
                "on_fail": "stop_and_report",
            },
        )


def _validate_reserve_buffer(
    allocation: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> None:
    """Ensure reserve buffer meets minimum threshold."""
    min_reserve = float(behaviour_cfg.get("min_reserve_buffer_pct", 5))
    actual_reserve = float(allocation.get("reserve_buffer_pct", 0))
    if actual_reserve < min_reserve:
        raise PipelineStopError(
            f"reserve_buffer_pct ({actual_reserve}%) is below minimum ({min_reserve}%)",
            detail={
                "field": "budget_allocation_split.reserve_buffer_pct",
                "actual": actual_reserve,
                "minimum": min_reserve,
                "on_fail": "stop_and_report",
            },
        )


# ---------------------------------------------------------------------------
# Deterministic budget allocation computation
# ---------------------------------------------------------------------------


def compute_budget_allocation(
    budget_definition: Dict[str, Any],
    allocation_split: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute per-resource-type envelopes and daily spend ceiling.

    Pure arithmetic — no LLM involved.
    """
    total = float(budget_definition.get("total_amount", 0))
    currency = budget_definition.get("currency", "INR")
    period = budget_definition.get("period", "monthly")

    compute_pct = float(allocation_split.get("compute_pct", 0))
    storage_pct = float(allocation_split.get("storage_pct", 0))
    network_pct = float(allocation_split.get("network_pct", 0))
    managed_services_pct = float(allocation_split.get("managed_services_pct", 0))
    reserve_buffer_pct = float(allocation_split.get("reserve_buffer_pct", 0))

    # Calculate envelopes
    compute_envelope = round(total * compute_pct / 100, 2)
    storage_envelope = round(total * storage_pct / 100, 2)
    network_envelope = round(total * network_pct / 100, 2)
    managed_services_envelope = round(total * managed_services_pct / 100, 2)
    reserve_buffer = round(total * reserve_buffer_pct / 100, 2)

    # Calculate daily max spend based on period
    days_in_period = _period_to_days(period)
    per_day_max_spend = round(total / days_in_period, 2) if days_in_period > 0 else 0

    return {
        "total_budget": total,
        "currency": currency,
        "period": period,
        "compute_envelope": compute_envelope,
        "storage_envelope": storage_envelope,
        "network_envelope": network_envelope,
        "managed_services_envelope": managed_services_envelope,
        "reserve_buffer": reserve_buffer,
        "per_day_max_spend": per_day_max_spend,
        "allocation_generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _period_to_days(period: str) -> int:
    """Map budget period to approximate number of days."""
    mapping = {
        "monthly": 30,
        "quarterly": 90,
        "half_yearly": 180,
        "annual": 365,
        "fixed": 30,  # default assumption for fixed period
    }
    return mapping.get(period.lower(), 30)


# ---------------------------------------------------------------------------
# Post-flight PPS threshold validation
# ---------------------------------------------------------------------------


def validate_pps_thresholds(
    pps_config: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate PPS thresholds are in correct order and apply defaults.

    Returns enriched PPS config. Raises PipelineStopError if order is invalid.
    """
    proceed_pct = float(pps_config.get(
        "proceed_ceiling_pct",
        behaviour_cfg.get("default_proceed_ceiling_pct", 70),
    ))
    pivot_pct = float(pps_config.get(
        "pivot_ceiling_pct",
        behaviour_cfg.get("default_pivot_ceiling_pct", 90),
    ))
    stop_pct = float(pps_config.get(
        "stop_trigger_pct",
        behaviour_cfg.get("default_stop_trigger_pct", 90),
    ))

    if proceed_pct >= pivot_pct:
        raise PipelineStopError(
            "invalid_pps_order: proceed_ceiling_pct must be less than pivot_ceiling_pct",
            detail={
                "proceed_ceiling_pct": proceed_pct,
                "pivot_ceiling_pct": pivot_pct,
                "on_fail": "stop_and_report",
            },
        )

    if pivot_pct > stop_pct:
        raise PipelineStopError(
            "invalid_pps_order: pivot_ceiling_pct must not exceed stop_trigger_pct",
            detail={
                "pivot_ceiling_pct": pivot_pct,
                "stop_trigger_pct": stop_pct,
                "on_fail": "stop_and_report",
            },
        )

    # Enrich with defaults where LLM omitted values
    pps_config.setdefault("proceed_ceiling_pct", proceed_pct)
    pps_config.setdefault("pivot_ceiling_pct", pivot_pct)
    pps_config.setdefault("stop_trigger_pct", stop_pct)

    return pps_config


# ---------------------------------------------------------------------------
# Project config record builder
# ---------------------------------------------------------------------------


def build_project_config_record(
    project_identity: Dict[str, Any],
    budget_allocation_plan: Dict[str, Any],
    pps_threshold_config: Dict[str, Any],
    parsed_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the master project config record from validated components.

    Deterministic assembly — overrides LLM-generated fields with
    authoritative values from upstream validation.
    """
    record = {
        "project_id": project_identity.get("project_id"),
        "project_name": project_identity.get("project_name"),
        "owner": project_identity.get("owner_name"),
        "owner_email": project_identity.get("owner_email"),
        "cost_centre_code": project_identity.get("cost_centre_code"),
        "business_unit": project_identity.get("business_unit"),
        "budget_envelope": budget_allocation_plan.get("total_budget"),
        "currency": budget_allocation_plan.get("currency"),
        "allocation_split": {
            "compute": budget_allocation_plan.get("compute_envelope"),
            "storage": budget_allocation_plan.get("storage_envelope"),
            "network": budget_allocation_plan.get("network_envelope"),
            "managed_services": budget_allocation_plan.get("managed_services_envelope"),
            "reserve_buffer": budget_allocation_plan.get("reserve_buffer"),
        },
        "pps_thresholds": {
            "proceed_ceiling_pct": pps_threshold_config.get("proceed_ceiling_pct"),
            "pivot_ceiling_pct": pps_threshold_config.get("pivot_ceiling_pct"),
            "stop_trigger_pct": pps_threshold_config.get("stop_trigger_pct"),
        },
        "activated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Merge any additional fields from LLM-generated config (approval_chain, etc.)
    for key in ("financial_guardrails", "approval_chain", "alert_config"):
        if key in parsed_config:
            record[key] = parsed_config[key]

    return record


# ---------------------------------------------------------------------------
# Activation summary
# ---------------------------------------------------------------------------


def build_activation_summary(
    project_identity: Dict[str, Any],
    budget_allocation_plan: Dict[str, Any],
    pps_threshold_config: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a human-readable activation summary for reporting."""
    return {
        "status": "activated",
        "project_name": project_identity.get("project_name"),
        "project_id": project_identity.get("project_id"),
        "total_budget": budget_allocation_plan.get("total_budget"),
        "currency": budget_allocation_plan.get("currency"),
        "period": budget_allocation_plan.get("period"),
        "per_day_max_spend": budget_allocation_plan.get("per_day_max_spend"),
        "zones": {
            "proceed": f"0% — {pps_threshold_config.get('proceed_ceiling_pct')}%",
            "pivot": f"{pps_threshold_config.get('proceed_ceiling_pct')}% — {pps_threshold_config.get('pivot_ceiling_pct')}%",
            "stop": f"≥ {pps_threshold_config.get('stop_trigger_pct')}%",
        },
        "blocking_conditions_checked": len(behaviour_cfg.get("blocking_conditions", [])),
        "autonomous_actions_enabled": len(behaviour_cfg.get("autonomous_actions", [])),
        "approval_required_actions": len(behaviour_cfg.get("approval_required_actions", [])),
        "never_autonomous_guardrails": len(behaviour_cfg.get("never_autonomous", [])),
    }
