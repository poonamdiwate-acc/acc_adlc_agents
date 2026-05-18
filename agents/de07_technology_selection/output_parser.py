"""DE-07 output parser - parse LLM response and extract structured output.

Takes the raw LLM text response, validates it's proper JSON, and returns
the parsed dictionary. Behaviour module handles further validation and
normalization.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def parse(raw_response: str) -> Dict[str, Any]:
    """Parse the LLM's text response into a structured dictionary.
    
    Expected structure:
    {
      "tech_stack_recommendations": [...],
      "tech_stack_summary": {...}
    }
    
    Raises:
        json.JSONDecodeError: if response is not valid JSON
        KeyError: if required top-level keys are missing
    """
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM response is not valid JSON: {e}")
    
    # Verify required top-level keys exist
    if "tech_stack_recommendations" not in parsed:
        raise KeyError("Missing required key: tech_stack_recommendations")
    if "tech_stack_summary" not in parsed:
        raise KeyError("Missing required key: tech_stack_summary")
    
    return parsed
