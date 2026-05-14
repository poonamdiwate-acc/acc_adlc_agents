"""PDF output renderer — renders HTML then converts via weasyprint."""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.output_renderers.base import OutputRenderer, RenderedOutput
from core.output_renderers.html_renderer import HtmlRenderer

logger = logging.getLogger(__name__)


class PdfRenderer(OutputRenderer):
    """Renders result as PDF by first generating HTML then converting."""

    @property
    def format_name(self) -> str:
        return "pdf"

    def render(
        self,
        result: Dict[str, Any],
        *,
        agent_id: str,
        run_id: str,
    ) -> RenderedOutput:
        try:
            from weasyprint import HTML
        except ImportError as exc:
            raise RuntimeError(
                "weasyprint is required for PDF rendering. "
                "Install with: pip install weasyprint"
            ) from exc

        # First generate HTML using the HTML renderer
        html_renderer = HtmlRenderer()
        html_output = html_renderer.render(result, agent_id=agent_id, run_id=run_id)
        html_string = html_output.content.decode("utf-8")

        # Convert HTML to PDF via weasyprint
        pdf_bytes = HTML(string=html_string).write_pdf()

        return RenderedOutput(
            content=pdf_bytes,
            content_type="application/pdf",
            filename=f"{agent_id}_output_{run_id}.pdf",
        )
