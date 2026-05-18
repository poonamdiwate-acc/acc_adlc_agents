"""PL-01 input builder — assemble the LLM user message.

The system prompt comes from the SKILL.md (loaded by ``skill_loader``).
This module owns the user message — the JSON-serialised inputs the LLM
needs to do its job. Layout matches what the SKILL's ``INPUTS:`` section
describes.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def build_user_message(payload: Dict[str, Any]) -> str:
    """Render the agent payload into the user message body."""
    body = {
        "structured_requirements": payload.get("structured_requirements") or [],
        "business_case": payload.get("business_case") or "",
        "project_context": payload.get("project_context") or {},
        "scope_boundaries": payload.get("scope_boundaries"),
    }
    # Include regulatory checklist only if loaded
    if payload.get("regulatory_checklist"):
        body["regulatory_checklist"] = payload["regulatory_checklist"]
    return json.dumps(body, ensure_ascii=False, indent=2)
