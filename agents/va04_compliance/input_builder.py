"""VA-04 input builder - assemble the LLM user message.

The system prompt comes from VA-04_Compliance_SKILL.md (loaded by
``skill_loader``). This module owns the user message — the JSON-serialised
inputs the LLM needs to perform compliance evaluation.

Layout matches what the SKILL's ``INPUTS:`` section describes:
- release_artefacts  (required array)
- policy_rules       (required array)
- project_context    (required object)
- business_case      (required string)
- constraints        (optional object)
"""

from __future__ import annotations

import json
from typing import Any, Dict


def build_user_message(payload: Dict[str, Any]) -> str:
    """Render the compliance payload into the LLM user message body.

    The shared folder merges all extracted fields flat into the payload.
    We extract the five compliance inputs directly and build the message.
    """
    body = {
        "release_artefacts": payload.get("release_artefacts") or [],
        "policy_rules":      payload.get("policy_rules") or [],
        "project_context":   payload.get("project_context") or {},
        "business_case":     payload.get("business_case") or "",
        "constraints":       payload.get("constraints"),
    }
    return json.dumps(body, ensure_ascii=False, indent=2)
