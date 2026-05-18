"""Markdown input parser — extracts text and Mermaid diagrams from markdown files."""

from __future__ import annotations

import re
from typing import List

from core.input_parsers.base import InputParser, ParsedDocument


class MarkdownParser(InputParser):
    """Extracts text, sections, and Mermaid diagrams from Markdown content."""

    @property
    def supported_content_types(self) -> List[str]:
        return ["text/markdown", "text/x-markdown"]

    def parse(self, content: bytes) -> ParsedDocument:
        if not content:
            raise ValueError("Empty Markdown input")

        # Decode content
        text_content = content.decode("utf-8", errors="replace")

        # Extract Mermaid diagrams
        mermaid_diagrams = _extract_mermaid_diagrams(text_content)

        # Extract sections by headers
        sections = _extract_markdown_sections(text_content)

        # Remove code blocks for raw text
        text_without_code = re.sub(r'```[\s\S]*?```', '', text_content)
        raw_text = text_without_code.strip()

        if not raw_text.strip():
            raise ValueError("Markdown file contains no text content")

        return ParsedDocument(
            raw_text=raw_text,
            sections=sections,
            tables=[],  # Markdown tables could be parsed if needed
            source_format="markdown",
            metadata={
                "size_bytes": len(content),
                "mermaid_diagram_count": len(mermaid_diagrams),
                "mermaid_diagrams": mermaid_diagrams,
                "section_count": len(sections),
            },
        )


def _extract_mermaid_diagrams(text: str) -> List[str]:
    """Extract all Mermaid code blocks from markdown."""
    mermaid_pattern = r'```mermaid\s*\n(.*?)\n```'
    matches = re.findall(mermaid_pattern, text, re.DOTALL | re.IGNORECASE)
    return [match.strip() for match in matches]


def _extract_markdown_sections(text: str) -> dict[str, str]:
    """Extract content grouped by markdown headers."""
    sections: dict[str, str] = {}
    lines = text.split('\n')
    current_heading = "_preamble"
    current_parts: list[str] = []

    for line in lines:
        # Check if line is a header (starts with #)
        header_match = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
        if header_match:
            # Save previous section
            if current_parts:
                sections[current_heading] = '\n'.join(current_parts).strip()
            # Start new section
            current_heading = header_match.group(2).strip()
            current_parts = []
        else:
            if line.strip():
                current_parts.append(line)

    # Save last section
    if current_parts:
        sections[current_heading] = '\n'.join(current_parts).strip()

    return sections
