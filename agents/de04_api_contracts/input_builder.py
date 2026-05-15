"""DE-04 input builder - assemble the LLM user message.

The system prompt comes from SKILL.md (loaded by ``skill_loader``). This
module owns the user message - the JSON-serialised inputs the LLM needs
to do its job. Layout matches what the SKILL's ``INPUTS:`` section
describes.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def build_user_message(payload: Dict[str, Any]) -> str:
    """Render the agent payload into the user message body.
    
    The shared folder merges all extracted fields flat into the payload.
    We extract structured_requirements and data_model directly from the
    payload and build the LLM input message.
    """
    body = {
        "structured_requirements": payload.get("structured_requirements") or [],
        "data_design_model_and_strategy": (
            payload.get("data_model") or 
            payload.get("data_design_model_and_strategy") or 
            []
        ),
        "project_context": payload.get("project_context") or {},
        "business_case": payload.get("business_case") or "",
        "constraints": payload.get("constraints"),
    }
    return json.dumps(body, ensure_ascii=False, indent=2)
