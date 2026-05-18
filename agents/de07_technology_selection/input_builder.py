"""DE-07 input builder - assemble the LLM user message.

The system prompt comes from SKILL.md (loaded by skill_loader). This
module owns the user message - the JSON-serialised inputs the LLM needs.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def build_user_message(payload: Dict[str, Any]) -> str:
    """Render the agent payload into the user message body.
    
    The shared folder merges all extracted fields flat into the payload.
    DOCX/PDF parsing returns keys like:
    - functional_requirements
    - non_functional_requirements
    - business_rules_and_constraints
    - project_context
    
    We send all requirement fields + agent_architecture to the LLM.
    """
    agent_arch = payload.get("agent_architecture") or {}
    
    # Extract all requirement-related fields (everything except agent_architecture)
    requirements_data = {k: v for k, v in payload.items() if k != "agent_architecture"}
    
    body = {
        "requirements": requirements_data,
        "agent_architecture": agent_arch,
    }
    return json.dumps(body, ensure_ascii=False, indent=2)
