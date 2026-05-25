"""VA-05 input builder — assemble the LLM user message.

The system prompt comes from the SKILL.md (loaded by ``skill_loader``).
This module owns the user message — the JSON-serialised inputs the LLM
needs to disposition exceptions and (optionally) raise judgment-based
audit findings. Layout matches what the SKILL's ``INPUTS:`` section
describes.

Pre-computed audit_findings (chronology / duplicate / sequence checks
performed deterministically by ``behaviour``) are passed in too so the
LLM does not invent them — it only adds judgment-based findings on top.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


def build_user_message(
    payload: Dict[str, Any],
    *,
    precomputed_findings: List[Dict[str, Any]] | None = None,
) -> str:
    """Render the agent payload into the user message body."""
    body: Dict[str, Any] = {
        "audit_trail": payload.get("audit_trail") or [],
        "exception_flags": payload.get("exception_flags") or [],
        "project_context": payload.get("project_context") or {},
        "business_case": payload.get("business_case") or "",
    }
    if payload.get("checkpoint_expectations") is not None:
        body["checkpoint_expectations"] = payload["checkpoint_expectations"]
    if precomputed_findings:
        body["precomputed_audit_findings"] = precomputed_findings
    return json.dumps(body, ensure_ascii=False, indent=2)
