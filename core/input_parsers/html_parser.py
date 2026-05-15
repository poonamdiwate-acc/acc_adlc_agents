"""HTML input parser — extracts text from free-form HTML documents."""

from __future__ import annotations

from typing import List

from core.input_parsers.base import InputParser, ParsedDocument


class HtmlParser(InputParser):
    """Extracts text, sections, and tables from HTML content."""

    @property
    def supported_content_types(self) -> List[str]:
        return ["text/html"]

    def parse(self, content: bytes) -> ParsedDocument:
        if not content:
            raise ValueError("Empty HTML input")

        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise RuntimeError(
                "beautifulsoup4 is required for HTML parsing. "
                "Install with: pip install beautifulsoup4"
            ) from exc

        # Detect encoding, default to utf-8
        text_content = content.decode("utf-8", errors="replace")

        soup = BeautifulSoup(text_content, "html.parser")

        # Remove script and style elements
        for element in soup(["script", "style"]):
            element.decompose()

        # Extract full text
        raw_text = soup.get_text(separator="\n", strip=True)
        if not raw_text.strip():
            raise ValueError("HTML file contains no text content")

        # Extract sections by headings (h1-h6)
        sections = _extract_sections(soup)

        # Extract tables
        tables = _extract_tables(soup)

        return ParsedDocument(
            raw_text=raw_text,
            sections=sections,
            tables=tables,
            source_format="html",
            metadata={
                "size_bytes": len(content),
                "title": soup.title.string if soup.title else None,
                "table_count": len(tables),
                "section_count": len(sections),
            },
        )


def _extract_sections(soup) -> dict[str, str]:
    """Extract content grouped by heading elements."""
    sections: dict[str, str] = {}
    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}
    current_heading = "_preamble"
    current_parts: list[str] = []

    for element in soup.body.children if soup.body else soup.children:
        if hasattr(element, "name") and element.name in heading_tags:
            if current_parts:
                sections[current_heading] = "\n".join(current_parts)
            current_heading = element.get_text(strip=True)
            current_parts = []
        elif hasattr(element, "get_text"):
            text = element.get_text(strip=True)
            if text:
                current_parts.append(text)

    if current_parts:
        sections[current_heading] = "\n".join(current_parts)

    return sections


def _extract_tables(soup) -> list[list[list[str]]]:
    """Extract all HTML tables as lists of rows."""
    tables: list[list[list[str]]] = []
    for table in soup.find_all("table"):
        table_data: list[list[str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            row_data = [cell.get_text(strip=True) for cell in cells]
            table_data.append(row_data)
        if table_data:
            tables.append(table_data)
    return tables
