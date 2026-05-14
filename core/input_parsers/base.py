"""Base class and data model for input parsers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParsedDocument:
    """Normalized extraction result from any input format.

    For free-form documents, ``raw_text`` contains the full extracted text
    and ``sections`` maps heading names to their body text. The LLM uses
    these to produce the structured payload the agent expects.

    For JSON inputs, ``structured_data`` is populated directly and no LLM
    extraction step is needed.
    """

    raw_text: str = ""
    sections: Dict[str, str] = field(default_factory=dict)
    tables: List[List[List[str]]] = field(default_factory=list)
    structured_data: Optional[Dict[str, Any]] = None
    source_format: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def needs_llm_extraction(self) -> bool:
        """True when the content is free-form and needs LLM structuring."""
        return self.structured_data is None


class InputParser(ABC):
    """Base class for all format-specific input parsers."""

    @abstractmethod
    def parse(self, content: bytes) -> ParsedDocument:
        """Parse raw file bytes into a :class:`ParsedDocument`.

        Raises :class:`ValueError` if the content is malformed or empty.
        """

    @property
    @abstractmethod
    def supported_content_types(self) -> List[str]:
        """MIME types this parser handles."""
