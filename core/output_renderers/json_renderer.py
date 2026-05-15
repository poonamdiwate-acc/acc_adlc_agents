"""JSON output renderer — default passthrough."""

from __future__ import annotations

import json
from typing import Any, Dict

from core.output_renderers.base import OutputRenderer, RenderedOutput


class JsonRenderer(OutputRenderer):
    """Renders result as JSON bytes (current default behaviour)."""

    @property
    def format_name(self) -> str:
        return "json"

    def render(
        self,
        result: Dict[str, Any],
        *,
        agent_id: str,
        run_id: str,
    ) -> RenderedOutput:
        content = json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8")
        return RenderedOutput(
            content=content,
            content_type="application/json",
            filename=f"{agent_id}_output_{run_id}.json",
        )
