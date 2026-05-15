"""DE-03 input builder - assemble the LLM user message.

The system prompt comes from the SKILL.md (loaded by ``skill_loader``).
This module owns the user message - the JSON-serialised inputs the LLM
needs to do its job. Layout matches what the SKILL's ``INPUTS:`` section
describes.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def build_user_message(payload: Dict[str, Any]) -> str:
    """Render the agent payload into the user message body.

    ``volume_estimates`` and ``nfr_constraints`` are optional — when the
    requirements doc does not include them the corresponding extraction
    keys are absent and the SKILL is expected to fall back to qualitative
    NFR reasoning from ``structured_requirements``.
    """
    body: Dict[str, Any] = {
        "structured_requirements": payload.get("structured_requirements") or [],
    }
    volume_estimates = payload.get("volume_estimates")
    if volume_estimates:
        body["volume_estimates"] = volume_estimates
    nfr_constraints = payload.get("nfr_constraints")
    if nfr_constraints:
        body["nfr_constraints"] = nfr_constraints
    return json.dumps(body, ensure_ascii=False, indent=2)
