"""Multi-format output renderers.

Each renderer converts a structured result dict into a specific
document format (JSON, DOCX, PDF, HTML).
"""

from core.output_renderers.base import OutputRenderer
from core.output_renderers.json_renderer import JsonRenderer
from core.output_renderers.docx_renderer import DocxRenderer
from core.output_renderers.pdf_renderer import PdfRenderer
from core.output_renderers.html_renderer import HtmlRenderer

__all__ = [
    "OutputRenderer",
    "JsonRenderer",
    "DocxRenderer",
    "PdfRenderer",
    "HtmlRenderer",
]
