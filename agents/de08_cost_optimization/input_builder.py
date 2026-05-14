"""DE-08 input builder — assemble the LLM user message.

The system prompt comes from the SKILL.md (loaded by ``skill_loader``).
This module owns the user message — the JSON-serialised inputs the LLM
needs to do its job.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def build_user_message(payload: Dict[str, Any]) -> str:
    """Render the agent payload into the user message body."""
    body = {
        "structured_requirements": payload.get("structured_requirements") or [],
        "agent_network_html": payload.get("agent_network_html") or "",
    }
    return json.dumps(body, ensure_ascii=False, indent=2)
