"""PDF input parser — extracts text from free-form PDF documents."""

from __future__ import annotations

import io
from typing import List

from core.input_parsers.base import InputParser, ParsedDocument


class PdfParser(InputParser):
    """Extracts text content from PDF files using PyPDF2."""

    @property
    def supported_content_types(self) -> List[str]:
        return ["application/pdf"]

    def parse(self, content: bytes) -> ParsedDocument:
        if not content:
            raise ValueError("Empty PDF input")

        try:
            from PyPDF2 import PdfReader
        except ImportError as exc:
            raise RuntimeError(
                "PyPDF2 is required for PDF parsing. "
                "Install with: pip install PyPDF2"
            ) from exc

        try:
            reader = PdfReader(io.BytesIO(content))
        except Exception as exc:
            raise ValueError(f"Invalid PDF file: {exc}") from exc

        page_texts: list[str] = []
        for page in reader.pages:
            text = page.extract_text()
            if text and text.strip():
                page_texts.append(text.strip())

        raw_text = "\n\n".join(page_texts)
        if not raw_text.strip():
            raise ValueError("PDF file contains no extractable text")

        # Attempt basic section detection via line patterns
        sections = _detect_sections(raw_text)

        return ParsedDocument(
            raw_text=raw_text,
            sections=sections,
            source_format="pdf",
            metadata={
                "size_bytes": len(content),
                "page_count": len(reader.pages),
                "pages_with_text": len(page_texts),
            },
        )


def _detect_sections(text: str) -> dict[str, str]:
    """Heuristic section detection from PDF text.

    Looks for lines that are short, title-cased, and followed by longer
    content. This is best-effort — the LLM handles the real structuring.
    """
    lines = text.split("\n")
    sections: dict[str, str] = {}
    current_heading = "_preamble"
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Heuristic: short all-caps or title-case line = heading
        if (
            len(stripped) < 80
            and not stripped.endswith(".")
            and (stripped.isupper() or stripped.istitle())
            and len(stripped.split()) <= 8
        ):
            if current_lines:
                sections[current_heading] = "\n".join(current_lines)
            current_heading = stripped
            current_lines = []
        else:
            current_lines.append(stripped)

    if current_lines:
        sections[current_heading] = "\n".join(current_lines)

    return sections
