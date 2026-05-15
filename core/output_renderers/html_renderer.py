"""HTML output renderer — Jinja2 template-based."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from core.output_renderers.base import OutputRenderer, RenderedOutput

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


class HtmlRenderer(OutputRenderer):
    """Renders result as a styled HTML report using Jinja2 templates."""

    @property
    def format_name(self) -> str:
        return "html"

    def render(
        self,
        result: Dict[str, Any],
        *,
        agent_id: str,
        run_id: str,
    ) -> RenderedOutput:
        try:
            from jinja2 import Environment, FileSystemLoader
        except ImportError as exc:
            raise RuntimeError(
                "Jinja2 is required for HTML rendering. "
                "Install with: pip install Jinja2"
            ) from exc

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=True,
        )

        template_name = f"{agent_id.lower().replace('-', '_')}_report.html.j2"
        # Fall back to generic template if agent-specific one doesn't exist
        template_path = _TEMPLATES_DIR / template_name
        if not template_path.is_file():
            template_name = "generic_report.html.j2"

        template = env.get_template(template_name)
        html_content = template.render(
            agent_id=agent_id,
            run_id=run_id,
            result=result,
            # AD-04 (Gap Detection) fields
            gap_report=result.get("gap_report", []),
            gap_summary=result.get("gap_summary", {}),
            # DE-03 (Data Design) fields
            data_model=result.get("data_model", []),
            storage_selection=result.get("storage_selection", {}),
        )

        content = html_content.encode("utf-8")
        return RenderedOutput(
            content=content,
            content_type="text/html; charset=utf-8",
            filename=f"{agent_id}_output_{run_id}.html",
        )
