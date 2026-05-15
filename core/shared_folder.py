"""Shared folder I/O — reads inputs from and writes outputs to a thread folder.

Every agent is configured with:
- ``shared_io.input_subfolder`` — folder name within the thread directory to read from
- ``shared_io.output_subfolder`` — folder name within the thread directory to write to

The system-wide ``shared_folder.base_path`` is declared in
``ADLC_Tech_Stack_Config.json``. Thread ID comes from the ``X-Thread-ID``
header on every request.

Directory layout::

    {base_path}/
    └── {thread_id}/
        ├── {input_subfolder}/      ← agent reads all files here
        │   ├── business_spec.docx
        │   ├── requirements.pdf
        │   └── scope.json
        └── {output_subfolder}/     ← agent writes result here
            └── PL-01_output.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.format_handler import parse_input

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".json", ".docx", ".pdf", ".html", ".htm", ".doc"}


def resolve_input_folder(
    base_path: str,
    thread_id: str,
    input_subfolder: str,
) -> Path:
    """Resolve and validate the input folder path.

    Returns the absolute path. Raises ValueError if it doesn't exist.
    """
    folder = Path(base_path) / thread_id / input_subfolder
    if not folder.is_dir():
        raise ValueError(
            f"Input folder not found: {folder}. "
            f"Ensure the thread '{thread_id}' has a '{input_subfolder}' subfolder."
        )
    return folder


def resolve_output_folder(
    base_path: str,
    thread_id: str,
    output_subfolder: str,
) -> Path:
    """Resolve and create (if needed) the output folder path."""
    folder = Path(base_path) / thread_id / output_subfolder
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def list_input_files(folder: Path) -> List[Path]:
    """List all supported files in the input folder (non-recursive)."""
    files = []
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS:
            files.append(f)
    if not files:
        raise ValueError(
            f"No supported input files found in {folder}. "
            f"Supported extensions: {sorted(_SUPPORTED_EXTENSIONS)}"
        )
    return files


async def read_inputs(
    base_path: str,
    thread_id: str,
    input_subfolder: str,
    *,
    agent_id: str,
    llm_client: Any = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Read and merge all input files from the shared folder into a payload.

    Each file is parsed according to its format. JSON files contribute
    structured data directly. Free-form files (docx, pdf, html) are
    parsed for text and then LLM-extracted into structured fields.

    Multiple files are merged into a single dict (last writer wins for
    overlapping keys).
    """
    folder = resolve_input_folder(base_path, thread_id, input_subfolder)
    files = list_input_files(folder)

    logger.info(
        "Shared folder read: agent=%s thread=%s folder=%s files=%d",
        agent_id, thread_id, folder, len(files),
    )

    merged_payload: Dict[str, Any] = {}

    for file_path in files:
        content_type = _get_content_type(file_path)
        file_bytes = file_path.read_bytes()

        logger.info(
            "Parsing input file: %s (content_type=%s, size=%d bytes)",
            file_path.name, content_type, len(file_bytes),
        )

        parsed = await parse_input(
            content=file_bytes,
            content_type=content_type,
            filename=file_path.name,
            agent_id=agent_id,
            llm_client=llm_client,
            llm_config=llm_config,
        )

        # Merge parsed data into the payload
        merged_payload.update(parsed)

    logger.info(
        "Shared folder merge complete: agent=%s keys=%s",
        agent_id, list(merged_payload.keys()),
    )
    return merged_payload


def write_output(
    base_path: str,
    thread_id: str,
    output_subfolder: str,
    *,
    agent_id: str,
    result: Dict[str, Any],
    output_format: str = "json",
) -> Path:
    """Write the agent result to the output folder.

    Returns the path of the written file.
    """
    folder = resolve_output_folder(base_path, thread_id, output_subfolder)

    if output_format == "json":
        out_path = folder / f"{agent_id}_output.json"
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        # For non-JSON formats, render via format_handler
        from core.format_handler import render_output
        rendered = render_output(
            result, output_format, agent_id=agent_id, run_id=thread_id
        )
        out_path = folder / rendered.filename
        out_path.write_bytes(rendered.content)

    logger.info("Output written: %s", out_path)
    return out_path


def _get_content_type(file_path: Path) -> str:
    """Map file extension to MIME content-type."""
    ext = file_path.suffix.lower()
    mapping = {
        ".json": "application/json",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".html": "text/html",
        ".htm": "text/html",
    }
    return mapping.get(ext, "application/octet-stream")
