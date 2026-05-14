"""Shared multi-format handler for all agents.

This module provides:
1. ``parse_input()`` — detect format + extract content from any supported
   input type (json, docx, pdf, html). For free-form documents, triggers
   an LLM call to structure the content.
2. ``render_output()`` — convert a result dict into the requested output
   format (json, docx, pdf, html).
3. ``get_parser_for_content_type()`` / ``get_renderer_for_format()`` —
   factory helpers.

Used by the API router layer. Agents themselves always work with the
internal dict representation and are format-agnostic.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from core.input_parsers import (
    DocxParser,
    HtmlParser,
    InputParser,
    JsonParser,
    ParsedDocument,
    PdfParser,
)
from core.output_renderers import (
    DocxRenderer,
    HtmlRenderer,
    JsonRenderer,
    OutputRenderer,
    PdfRenderer,
)
from core.output_renderers.base import RenderedOutput

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Parser registry — maps content-type prefixes to parser instances
# --------------------------------------------------------------------------

_PARSERS: list[InputParser] = [
    JsonParser(),
    DocxParser(),
    PdfParser(),
    HtmlParser(),
]

_RENDERERS: Dict[str, OutputRenderer] = {
    "json": JsonRenderer(),
    "docx": DocxRenderer(),
    "pdf": PdfRenderer(),
    "html": HtmlRenderer(),
}

SUPPORTED_INPUT_FORMATS = ["json", "docx", "pdf", "html"]
SUPPORTED_OUTPUT_FORMATS = list(_RENDERERS.keys())


def get_parser_for_content_type(content_type: str) -> Optional[InputParser]:
    """Return the parser matching the given MIME content-type, or None."""
    ct_lower = content_type.lower().split(";")[0].strip()
    for parser in _PARSERS:
        if ct_lower in parser.supported_content_types:
            return parser
    return None


def get_parser_for_extension(filename: str) -> Optional[InputParser]:
    """Return the parser matching the file extension, or None."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext_map = {
        "json": JsonParser(),
        "docx": DocxParser(),
        "pdf": PdfParser(),
        "html": HtmlParser(),
        "htm": HtmlParser(),
    }
    return ext_map.get(ext)


def get_renderer_for_format(format_name: str) -> Optional[OutputRenderer]:
    """Return the renderer for the given format name, or None."""
    return _RENDERERS.get(format_name.lower())


async def parse_input(
    content: bytes,
    content_type: str,
    filename: Optional[str] = None,
    *,
    agent_id: str,
    llm_client: Any = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Parse input bytes into the structured dict an agent expects.

    For JSON inputs, this is a direct parse. For free-form documents
    (docx, pdf, html), the text is extracted and then an LLM call
    structures it into the expected agent input schema.

    Args:
        content: Raw file bytes.
        content_type: MIME type of the content.
        filename: Original filename (used for extension-based fallback).
        agent_id: The target agent (used to pick the extraction prompt).
        llm_client: An LLMClient instance for free-form extraction.
        llm_config: LLM config dict for the extraction call.

    Returns:
        Structured dict matching the agent's expected input schema.

    Raises:
        ValueError: If format is unsupported or content is invalid.
    """
    # Resolve parser
    parser = get_parser_for_content_type(content_type)
    if parser is None and filename:
        parser = get_parser_for_extension(filename)
    if parser is None:
        raise ValueError(
            f"Unsupported input format: content_type={content_type!r}, "
            f"filename={filename!r}. "
            f"Supported: {SUPPORTED_INPUT_FORMATS}"
        )

    # Parse the document
    parsed: ParsedDocument = parser.parse(content)

    # JSON → already structured, return directly
    if not parsed.needs_llm_extraction:
        return parsed.structured_data  # type: ignore[return-value]

    # Free-form → LLM extraction
    if llm_client is None:
        raise ValueError(
            "Free-form document input requires an LLM client for "
            "structured extraction, but none was provided."
        )

    structured = await _llm_extract(
        parsed, agent_id=agent_id, llm_client=llm_client, llm_config=llm_config
    )
    return structured


async def _llm_extract(
    doc: ParsedDocument,
    *,
    agent_id: str,
    llm_client: Any,
    llm_config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Use an LLM to extract structured data from free-form text."""
    extraction_prompt = _build_extraction_prompt(doc, agent_id)

    # Use higher max_tokens and timeout for extraction — large documents need room.
    # Remove response_format constraint — thinking models may truncate structured
    # output when forced to JSON mode. We parse the JSON ourselves after.
    extraction_config = dict(llm_config or {})
    extraction_config["max_tokens"] = max(
        extraction_config.get("max_tokens", 8192), 32768
    )
    extraction_config["timeout_seconds"] = max(
        extraction_config.get("timeout_seconds", 60), 180
    )
    extraction_config.pop("response_format", None)

    raw_text = await llm_client.call(
        system_prompt=_EXTRACTION_SYSTEM_PROMPT,
        user_message=extraction_prompt,
        config=extraction_config,
        agent_id=f"{agent_id}-input-extraction",
    )

    # Parse the LLM's JSON response
    text = raw_text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # remove opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM extraction failed to produce valid JSON: {exc}"
        ) from exc

    if not isinstance(result, dict):
        raise ValueError("LLM extraction must return a JSON object")

    logger.info(
        "LLM extraction complete: agent=%s source_format=%s keys=%s",
        agent_id, doc.source_format, list(result.keys()),
    )
    return result


def _build_extraction_prompt(doc: ParsedDocument, agent_id: str) -> str:
    """Build the user message for LLM-based extraction."""
    parts = [
        f"Extract structured data from the following {doc.source_format.upper()} "
        f"document for agent {agent_id}.",
        "",
        "=== DOCUMENT TEXT ===",
        doc.raw_text,
    ]

    if doc.sections:
        parts.append("")
        parts.append("=== DETECTED SECTIONS ===")
        for heading, body in doc.sections.items():
            parts.append(f"\n## {heading}")
            parts.append(body)

    if doc.tables:
        parts.append("")
        parts.append("=== TABLES ===")
        for i, table in enumerate(doc.tables, 1):
            parts.append(f"\nTable {i}:")
            for row in table:
                parts.append(" | ".join(row))

    return "\n".join(parts)


_EXTRACTION_SYSTEM_PROMPT = """\
You are a document extraction assistant. Your job is to extract structured \
data from free-form documents and return it as a JSON object.

For gap detection input, extract:
- "structured_requirements": array of requirement objects, each with \
req_id, title, description, type, priority, acceptance_criterion, source_ref
- "business_case": string — the business case / justification text
- "project_context": object with squad, domain, project_name
- "scope_boundaries": object with in_scope (array) and out_of_scope (array), \
or null if not found

Rules:
1. Extract ALL requirements you can identify from the document.
2. If a requirement has no explicit ID, assign one as REQ-001, REQ-002, etc.
3. If priority is not stated, infer from context or default to "medium".
4. If acceptance_criterion is not stated, set to empty string.
5. Return ONLY the JSON object. No explanation, no markdown, no preamble.
"""


def render_output(
    result: Dict[str, Any],
    output_format: str,
    *,
    agent_id: str,
    run_id: str,
) -> RenderedOutput:
    """Render an agent's result dict into the requested output format.

    Args:
        result: The agent's structured output.
        output_format: Target format (json, docx, pdf, html).
        agent_id: Agent identifier for filenames/titles.
        run_id: Run identifier for filenames.

    Returns:
        RenderedOutput with content bytes, MIME type, and filename.

    Raises:
        ValueError: If the output format is unsupported.
    """
    renderer = get_renderer_for_format(output_format)
    if renderer is None:
        raise ValueError(
            f"Unsupported output format: {output_format!r}. "
            f"Supported: {SUPPORTED_OUTPUT_FORMATS}"
        )

    return renderer.render(result, agent_id=agent_id, run_id=run_id)
