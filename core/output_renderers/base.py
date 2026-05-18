"""Base class for output renderers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class RenderedOutput:
    """Result of rendering an agent output to a specific format."""

    content: bytes
    content_type: str
    filename: str


class OutputRenderer(ABC):
    """Base class for all format-specific output renderers."""

    @abstractmethod
    def render(
        self,
        result: Dict[str, Any],
        *,
        agent_id: str,
        run_id: str,
    ) -> RenderedOutput:
        """Render a result dict into the target format.

        Args:
            result: The agent's structured output (gap_report, gap_summary, etc.)
            agent_id: Agent identifier (e.g. "PL-01")
            run_id: Run identifier for filenames

        Returns:
            RenderedOutput with bytes, content-type, and suggested filename.
        """

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Short format identifier (json, docx, pdf, html)."""
