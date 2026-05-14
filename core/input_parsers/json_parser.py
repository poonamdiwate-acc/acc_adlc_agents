"""JSON input parser — passthrough for structured payloads."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from core.input_parsers.base import InputParser, ParsedDocument


class JsonParser(InputParser):
    """Parses JSON bytes directly into structured data (no LLM needed)."""

    @property
    def supported_content_types(self) -> List[str]:
        return ["application/json"]

    def parse(self, content: bytes) -> ParsedDocument:
        text = content.decode("utf-8").strip()
        if not text:
            raise ValueError("Empty JSON input")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError("JSON input must be an object (dict)")

        return ParsedDocument(
            raw_text=text,
            structured_data=data,
            source_format="json",
            metadata={"size_bytes": len(content)},
        )
