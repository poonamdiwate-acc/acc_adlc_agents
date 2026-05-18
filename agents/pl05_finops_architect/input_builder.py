"""PL-05 input builder — assemble the LLM user message.

The system prompt comes from the SKILL.md (loaded by ``skill_loader``).
This module owns the user message — the JSON-serialised inputs the LLM
needs to generate PPS threshold config, forecast baseline, and project
config record.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def build_user_message(payload: Dict[str, Any]) -> str:
    """Render the agent payload into the user message body."""
    body = {
        "project_identity": payload.get("project_identity") or {},
        "budget_definition": payload.get("budget_definition") or {},
        "budget_allocation_split": payload.get("budget_allocation_split") or {},
        "cloud_environment": payload.get("cloud_environment") or {},
    }
    return json.dumps(body, ensure_ascii=False, indent=2)
