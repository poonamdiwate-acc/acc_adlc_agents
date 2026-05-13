"""ADLC pipeline exception hierarchy.

All ADLC errors inherit from :class:`ADLCError` so callers can catch the whole
tree with a single ``except``. Each exception carries a human-readable message
and an optional ``detail`` dict for structured logging and pipeline_status
payloads.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ADLCError(Exception):
    """Base class for every error raised inside the ADLC pipeline."""

    def __init__(
        self,
        message: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.detail: Dict[str, Any] = dict(detail) if detail else {}


class PipelineStopError(ADLCError):
    """Raised when an unrecoverable condition halts pipeline execution."""


class OutputParseError(ADLCError):
    """Raised when LLM output fails JSON parsing or Pydantic validation."""


class LLMCallError(ADLCError):
    """Raised when a call to the LLM fails (timeout, rate limit, transport)."""


class ConfigLoadError(ADLCError):
    """Raised when a JSON config file cannot be located, read, or parsed."""


class SkillLoadError(ADLCError):
    """Raised when a SKILL.md file cannot be located or parsed."""


class GitReadError(ADLCError):
    """Raised when an audit-repo read fails (file missing, invalid JSON,
    auth/network error against the remote)."""
