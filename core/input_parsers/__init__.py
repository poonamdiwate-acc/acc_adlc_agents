"""Multi-format input parsers.

Each parser extracts raw text content from a document format.
The extracted text is then passed to the LLM for structured extraction
since inputs are free-form (no fixed template).
"""

from core.input_parsers.base import InputParser, ParsedDocument
from core.input_parsers.docx_parser import DocxParser
from core.input_parsers.pdf_parser import PdfParser
from core.input_parsers.html_parser import HtmlParser
from core.input_parsers.json_parser import JsonParser

__all__ = [
    "InputParser",
    "ParsedDocument",
    "DocxParser",
    "PdfParser",
    "HtmlParser",
    "JsonParser",
]
