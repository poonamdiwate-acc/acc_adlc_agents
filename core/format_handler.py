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
    MarkdownParser,
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
    MarkdownParser(),
]

_RENDERERS: Dict[str, OutputRenderer] = {
    "json": JsonRenderer(),
    "docx": DocxRenderer(),
    "pdf": PdfRenderer(),
    "html": HtmlRenderer(),
}

SUPPORTED_INPUT_FORMATS = ["json", "docx", "pdf", "html", "md"]
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
        "md": MarkdownParser(),
        "markdown": MarkdownParser(),
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
    """Use an LLM to extract structured data from free-form text.

    Gemini occasionally emits stray ``]``/``}`` mid-object or other JSON
    syntax glitches that are not recoverable from the bytes alone. We
    retry the call once when that happens — the same prompt usually
    succeeds the second time.
    """
    extraction_prompt = _build_extraction_prompt(doc, agent_id)

    extraction_config = dict(llm_config or {})
    extraction_config["max_tokens"] = max(
        extraction_config.get("max_tokens", 8192), 32768
    )
    extraction_config["timeout_seconds"] = max(
        extraction_config.get("timeout_seconds", 60), 180
    )
    extraction_config.pop("response_format", None)

    attempts = 2
    last_err: Optional[ValueError] = None
    for attempt in range(1, attempts + 1):
        raw_text = await llm_client.call(
            system_prompt=_extraction_prompt_for(agent_id),
            user_message=extraction_prompt,
            config=extraction_config,
            agent_id=f"{agent_id}-input-extraction",
        )
        try:
            result = _parse_extraction_json(raw_text)
            if attempt > 1:
                logger.warning(
                    "LLM extraction succeeded on retry: agent=%s attempt=%d",
                    agent_id, attempt,
                )
            logger.info(
                "LLM extraction complete: agent=%s source_format=%s keys=%s",
                agent_id, doc.source_format, list(result.keys()),
            )
            return result
        except ValueError as exc:
            last_err = exc
            if attempt < attempts:
                logger.warning(
                    "LLM extraction parse failed (attempt %d/%d), retrying: "
                    "agent=%s err=%s",
                    attempt, attempts, agent_id, exc,
                )
            else:
                logger.warning(
                    "LLM extraction parse failed (attempt %d/%d), giving up: "
                    "agent=%s err=%s",
                    attempt, attempts, agent_id, exc,
                )

    # All attempts failed — raise the last error as-is.
    raise last_err  # type: ignore[misc]


def _parse_extraction_json(raw_text: str) -> Dict[str, Any]:
    """Parse one JSON object out of raw LLM text.

    Strips outer markdown fences, skips ahead to the first ``{``, and
    uses ``JSONDecoder.raw_decode`` so trailing prose / extra JSON
    blocks after the main object are silently dropped.
    """
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    brace_idx = text.find("{")
    if brace_idx == -1:
        raise ValueError(
            "LLM extraction returned no JSON object (no '{' in response)"
        )
    text = text[brace_idx:]

    decoder = json.JSONDecoder()
    try:
        result, end_pos = decoder.raw_decode(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM extraction failed to produce valid JSON: {exc}"
        ) from exc

    trailing = text[end_pos:].strip()
    if trailing:
        logger.info(
            "LLM extraction: discarded %d chars of trailing content after JSON",
            len(trailing),
        )

    if not isinstance(result, dict):
        raise ValueError("LLM extraction must return a JSON object")

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


_AD04_EXTRACTION_PROMPT = """\
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


_DE03_EXTRACTION_PROMPT = """\
You are a document extraction assistant. Your job is to extract structured \
data from free-form documents and return it as a JSON object.

For data design input, extract any of the following keys that the \
document actually supports — omit any key you have no evidence for.

- "structured_requirements": array of requirement objects, each with \
req_id, title, description, type, priority, acceptance_criterion, source_ref.
- "volume_estimates": object with the shape::

      {
        "per_entity": [
          {
            "entity_name": "string",
            "initial_rows": integer | null,
            "growth_rate_yoy_pct": number | null,
            "avg_record_size_kb": number | null,
            "retention": "string (e.g. '7_years', 'indefinite')",
            "read_qps_peak": integer | null,
            "write_qps_peak": integer | null,
            "access_pattern": "read_heavy_point_lookup | write_heavy_append | scan_heavy_analytical | mixed"
          }
        ],
        "aggregate": {
          "total_storage_estimate_gb": number | null,
          "peak_concurrent_users":     integer | null,
          "data_lifecycle":             "string (e.g. 'hot_90d_warm_1y_cold_archive')",
          "compliance_zones":           ["PII", "PCI", "HIPAA"]
        }
      }

  Pull numbers verbatim if stated. If the doc says "millions of users" \
  without a number, set "initial_rows" to null and put the qualitative \
  phrase in a sibling "_notes" string.

- "nfr_constraints": object capturing non-functional constraints relevant \
to storage decisions, with the shape::

      {
        "latency_targets":   { "p50_ms": number | null, "p95_ms": number | null, "p99_ms": number | null },
        "availability_sla":  "string (e.g. '99.95%')",
        "durability":        "string (e.g. '11_nines')",
        "consistency_model": "strong | eventual | causal | bounded_staleness",
        "compliance_zones":  ["PII", "PCI", "HIPAA", "GDPR"],
        "data_residency":    "string (e.g. 'EU_only', 'IN_only')",
        "encryption":        "at_rest | in_transit | both | none"
      }

Rules:
1. Extract ALL requirements you can identify from the document.
2. If a requirement has no explicit ID, assign one as REQ-001, REQ-002, etc.
3. If priority is not stated, infer from context or default to "medium".
4. If acceptance_criterion is not stated, set to empty string.
5. Return ONLY the JSON object. No explanation, no markdown, no preamble.
6. Only include keys you actually populated — never invent empty placeholders.
7. For volume_estimates and nfr_constraints, prefer null over fabrication \
when the doc is silent on a sub-field.
"""


_GENERIC_EXTRACTION_PROMPT = """\
You are a document extraction assistant. Your job is to extract structured \
data from free-form documents and return it as a JSON object.

Extract every requirement, decision, constraint, or named entity that the \
document contains. Use clear top-level keys (e.g. "structured_requirements", \
"business_case", "project_context", "scope_boundaries", "constraints", \
"agent_network_html") only when the document actually contains data for \
that key. Do not invent empty placeholders.

Rules:
1. If a requirement has no explicit ID, assign one as REQ-001, REQ-002, etc.
2. If priority is not stated, infer from context or default to "medium".
3. Return ONLY the JSON object. No explanation, no markdown, no preamble.
"""


_DE04_EXTRACTION_PROMPT = """\
You are a document extraction assistant. Your job is to extract structured \
data from free-form documents and return it as a JSON object.

For API contracts input, extract any of the following keys that the \
document actually supports — omit any key you have no evidence for.

- "structured_requirements": SINGLE array of requirement objects covering \
BOTH functional and non-functional requirements found anywhere in the \
document (user stories, acceptance criteria, NFRs, business rules, \
integration requirements, compliance rules, etc.). Each object has \
{req_id, title, description, type, priority, acceptance_criterion, \
source_ref}. Do NOT split into separate "functional_requirements" / \
"non_functional_requirements" / "user_stories" arrays — unify them all \
into "structured_requirements".
- "project_context": object with {squad, domain, project_name} if the \
document mentions any of these. Skip otherwise.
- "business_case": string — concise summary of why the system exists, the \
business value, or the problem being solved. Skip if not present.
- "constraints": object capturing any explicit API/system design \
constraints (e.g. "must expose REST", "must support OAuth2", \
"rate-limit 1000 rps") — free-form keys allowed. Skip if not present.
- "data_model": ONLY include this if the source document already \
contains a finalised data-model definition with DM-### entity ids. \
Otherwise omit — the data model will be supplied separately from \
DE-03's output.

Rules:
1. Extract ALL requirements you can identify, merging functional + NFRs \
into one "structured_requirements" array.
2. If a requirement has no explicit ID, assign REQ-001, REQ-002, ... \
sequentially across the unified list.
3. Set "type" to "functional" or "non-functional" so DE-04 can still \
distinguish them.
4. If priority is not stated, infer from context or default to "medium".
5. If acceptance_criterion is not stated, set to "".
6. Return ONLY the JSON object. No explanation, no markdown, no preamble.
7. Only include keys you actually populated — never invent empty \
placeholders.
"""


_AGENT_EXTRACTION_PROMPTS: Dict[str, str] = {
    "AD-04": _AD04_EXTRACTION_PROMPT,
    "PL-01": _AD04_EXTRACTION_PROMPT,  # Gap detection uses same extraction as AD-04
    "DE-03": _DE03_EXTRACTION_PROMPT,
    "DE-04": _DE04_EXTRACTION_PROMPT,
}


def _extraction_prompt_for(agent_id: str) -> str:
    """Return the extraction system prompt tuned to ``agent_id``.

    Falls back to the generic prompt for agents that have not declared an
    extraction template. Keeps the format handler agent-agnostic at the
    code level while letting each agent's input schema drive the LLM.
    """
    return _AGENT_EXTRACTION_PROMPTS.get(agent_id, _GENERIC_EXTRACTION_PROMPT)


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
