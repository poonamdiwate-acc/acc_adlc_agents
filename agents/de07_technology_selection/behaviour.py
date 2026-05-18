"""DE-07 behaviour - config-driven validation and post-processing.

Handles:
1. Pre-flight validation - ensures NFRs, architecture, and constraints are present
2. Post-flight normalization - enforces sequential TS-### IDs, validates confidence scores
3. Registry recomputation - calculates overall confidence and sets recommendation
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.exceptions import PipelineStopError


def validate_inputs(
    payload: Dict[str, Any],
    inputs_cfg: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> None:
    """Raise :class:`PipelineStopError` if payload violates the config.
    
    Per ``behaviour.on_empty_requirements: stop_and_report`` an empty
    ``structured_requirements`` array stops the agent before any LLM call.
    
    Per ``behaviour.on_missing_agent_architecture: stop_and_report`` a missing
    ``agent_architecture`` object stops the agent.
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
                f"Input '{field_name}' has {len(value)} items, minimum {min_items}",
                detail={
                    "field": field_name,
                    "min_items": min_items,
                    "actual": len(value),
                },
            )

    # Validate at least one requirements field is present and not empty
    requirements_keys = ["functional_requirements", "non_functional_requirements", "user_stories"]
    has_requirements = any(
        payload.get(key) and isinstance(payload.get(key), list) and len(payload[key]) > 0
        for key in requirements_keys
    )
    
    if behaviour_cfg.get("on_empty_requirements") == "stop_and_report" and not has_requirements:
        raise PipelineStopError(
            f"No requirements found in any of: {requirements_keys}",
            detail={"on_empty_requirements": "stop_and_report"},
        )
    
    # Validate agent_architecture is present
    agent_arch = payload.get("agent_architecture")
    if behaviour_cfg.get("on_missing_agent_architecture") == "stop_and_report" and not agent_arch:
        raise PipelineStopError(
            "agent_architecture is missing",
            detail={"on_missing_agent_architecture": "stop_and_report"},
        )


def renumber_recommendations(
    tech_stack_recommendations: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Enforce sequential TS-### ids regardless of LLM output."""
    for index, rec in enumerate(tech_stack_recommendations, start=1):
        rec["recommendation_id"] = f"TS-{index:03d}"
    return tech_stack_recommendations


def validate_confidence_scores(
    tech_stack_recommendations: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Ensure all confidence scores are in range 0.0-1.0."""
    for rec in tech_stack_recommendations:
        score = rec.get("confidence_score", 0.0)
        if not isinstance(score, (int, float)) or score < 0.0 or score > 1.0:
            rec["confidence_score"] = 0.5  # Default to medium confidence
    return tech_stack_recommendations


def compute_summary(
    tech_stack_recommendations: List[Dict[str, Any]],
    behaviour_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Recompute tech_stack_summary from recommendations.
    
    For tech selection, we expect exactly 3 stack recommendations.
    The summary should identify the recommended stack (highest confidence).
    """
    total = len(tech_stack_recommendations)
    
    # Calculate overall confidence as the highest score (recommended stack)
    scores = [r.get("confidence_score", 0.0) for r in tech_stack_recommendations]
    overall_confidence = max(scores) if scores else 0.0
    
    # Find recommended stack (highest confidence)
    recommended = max(tech_stack_recommendations, key=lambda r: r.get("confidence_score", 0.0))
    recommended_stack = recommended.get("stack_name", "Unknown")
    recommended_stack_id = recommended.get("recommendation_id", "TS-001")
    
    # Extract stack names
    stacks = [r.get("stack_name", f"Stack {i+1}") for i, r in enumerate(tech_stack_recommendations)]
    
    # Determine recommendation based on thresholds
    thresholds = behaviour_cfg.get("recommendation_thresholds", {})
    proceed_threshold = thresholds.get("proceed", 0.8)
    review_threshold = thresholds.get("review_required", 0.6)
    
    if overall_confidence >= proceed_threshold:
        recommendation = "proceed"
    elif overall_confidence >= review_threshold:
        recommendation = "review_required"
    else:
        recommendation = "blocked"
    
    comparison_summary = (
        f"{recommended_stack} recommended with {overall_confidence:.0%} confidence. "
        f"Evaluated {total} technology stacks based on NFRs, constraints, and agent architecture."
    )
    
    return {
        "total_stacks_evaluated": total,
        "stacks": stacks,
        "recommended_stack": recommended_stack,
        "recommended_stack_id": recommended_stack_id,
        "overall_confidence": round(overall_confidence, 2),
        "comparison_summary": comparison_summary,
        "decision_factors": [],  # LLM should provide this
        "recommendation": recommendation,
    }


def postprocess_output(
    raw_output: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Normalize and validate the LLM output.
    
    For tech selection, we expect exactly 3 stack recommendations.
    """
    recommendations = raw_output.get("tech_stack_recommendations") or []
    
    if not recommendations:
        raise PipelineStopError(
            "No technology stack recommendations generated",
            detail={"on_no_recommendations": "stop_and_report"},
        )
    
    # Validate we have exactly 3 stacks
    if len(recommendations) != 3:
        raise PipelineStopError(
            f"Expected exactly 3 stack recommendations, got {len(recommendations)}",
            detail={"expected": 3, "actual": len(recommendations)},
        )
    
    # Normalize
    recommendations = renumber_recommendations(recommendations)
    recommendations = validate_confidence_scores(recommendations)
    
    # Recompute summary if not provided or incomplete
    summary = raw_output.get("tech_stack_summary") or {}
    if not summary or "recommended_stack" not in summary:
        summary = compute_summary(recommendations, behaviour_cfg)
    else:
        # Ensure key fields are present
        if "overall_confidence" not in summary:
            scores = [r.get("confidence_score", 0.0) for r in recommendations]
            summary["overall_confidence"] = round(max(scores), 2) if scores else 0.0
        if "recommendation" not in summary:
            conf = summary.get("overall_confidence", 0.0)
            thresholds = behaviour_cfg.get("recommendation_thresholds", {})
            if conf >= thresholds.get("proceed", 0.8):
                summary["recommendation"] = "proceed"
            elif conf >= thresholds.get("review_required", 0.6):
                summary["recommendation"] = "review_required"
            else:
                summary["recommendation"] = "blocked"
    
    return {
        "tech_stack_recommendations": recommendations,
        "tech_stack_summary": summary,
    }
