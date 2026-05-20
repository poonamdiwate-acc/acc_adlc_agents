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


def find_file_by_patterns(
    base_path: str,
    thread_id: str,
    subfolder: str,
    file_name_patterns: List[str],
    allowed_extensions: Optional[List[str]] = None,
) -> Optional[Path]:
    """Find first file matching any of the given name patterns.
    
    Args:
        base_path: Base shared folder path
        thread_id: Thread ID
        subfolder: Subfolder to search (use "." for thread root)
        file_name_patterns: List of exact file names to search for
        allowed_extensions: Optional list of allowed extensions (e.g., [".html", ".md"])
    
    Returns:
        Path to the first matching file, or None if not found
    """
    # Resolve folder
    if subfolder == ".":
        folder = Path(base_path) / thread_id
    else:
        folder = Path(base_path) / thread_id / subfolder
    
    if not folder.is_dir():
        logger.debug(
            "Folder not found for file search: %s (thread=%s, subfolder=%s)",
            folder, thread_id, subfolder
        )
        return None
    
    # Search for files matching patterns
    for pattern in file_name_patterns:
        for file_path in folder.iterdir():
            if not file_path.is_file():
                continue
            if _is_hidden_or_lockfile(file_path):
                continue
            
            # Check if filename matches pattern
            if file_path.name == pattern:
                # Check extension if specified
                if allowed_extensions is None or file_path.suffix.lower() in allowed_extensions:
                    logger.info(
                        "Found file by pattern: %s (thread=%s, pattern=%s)",
                        file_path.name, thread_id, pattern
                    )
                    return file_path
    
    return None


def list_input_files(folder: Path, allowed_extensions: Optional[List[str]] = None) -> List[Path]:
    """List all supported files in the input folder (non-recursive).

    Skips Office lock files (``~$foo.docx``), dotfiles, and other hidden
    OS artefacts. Word creates ``~$<name>`` lock files whenever a docx is
    open in the desktop app, and those would otherwise be picked up by
    extension and explode in the parsers.
    
    Args:
        folder: Directory to scan
        allowed_extensions: Optional list of extensions to filter (e.g., [".json"])
                          If None, all supported extensions are allowed.
    """
    extensions_to_use = set(allowed_extensions) if allowed_extensions else _SUPPORTED_EXTENSIONS
    files = []
    for f in sorted(folder.iterdir()):
        if f.name.startswith("~$"):
            continue
        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS:
            files.append(f)
    if not files:
        raise ValueError(
            f"No supported input files found in {folder}. "
            f"Supported extensions: {sorted(extensions_to_use)}"
        )
    return files


def _deep_merge(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Merge source into target, concatenating lists instead of overwriting."""
    for key, value in source.items():
        if (
            key in target
            and isinstance(target[key], list)
            and isinstance(value, list)
        ):
            seen_ids: set = set()
            for item in target[key]:
                if isinstance(item, dict) and "req_id" in item:
                    seen_ids.add(item["req_id"])
            for item in value:
                if isinstance(item, dict) and "req_id" in item:
                    if item["req_id"] not in seen_ids:
                        target[key].append(item)
                        seen_ids.add(item["req_id"])
                else:
                    target[key].append(item)
        elif (
            key in target
            and isinstance(target[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge(target[key], value)
        else:
            target[key] = value
def _is_hidden_or_lockfile(path: Path) -> bool:
    """True for Office lock files, dotfiles, and OS temp artefacts."""
    name = path.name
    if name.startswith("~$"):       # MS Office lock file (Word/Excel/PowerPoint)
        return True
    if name.startswith("."):         # dotfile (e.g. .DS_Store)
        return True
    if name.startswith("~"):         # legacy Office temp
        return True
    return False


async def read_inputs(
    base_path: str,
    thread_id: str,
    input_subfolder: str,
    *,
    agent_id: str,
    llm_client: Any = None,
    llm_config: Optional[Dict[str, Any]] = None,
    allowed_extensions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Read and merge all input files from the shared folder into a payload.

    Each file is parsed according to its format. JSON files contribute
    structured data directly. Free-form files (docx, pdf, html) are
    parsed for text and then LLM-extracted into structured fields.

    Multiple files are merged into a single dict (last writer wins for
    overlapping keys).
    
    Args:
        allowed_extensions: Optional list of extensions to filter (e.g., [".json"])
    """
    folder = resolve_input_folder(base_path, thread_id, input_subfolder)
    files = list_input_files(folder, allowed_extensions=allowed_extensions)

    logger.info(
        "Shared folder read: agent=%s thread=%s folder=%s files=%d",
        agent_id, thread_id, folder, len(files),
    )

    merged_payload: Dict[str, Any] = {}
    extraction_errors: List[str] = []

    for file_path in files:
        content_type = _get_content_type(file_path)
        file_bytes = file_path.read_bytes()

        logger.info(
            "Parsing input file: %s (content_type=%s, size=%d bytes)",
            file_path.name, content_type, len(file_bytes),
        )

        try:
            parsed = await parse_input(
                content=file_bytes,
                content_type=content_type,
                filename=file_path.name,
                agent_id=agent_id,
                llm_client=llm_client,
                llm_config=llm_config,
            )
            _deep_merge(merged_payload, parsed)
        except Exception as exc:
            logger.warning(
                "Failed to parse %s (continuing with other files): %s",
                file_path.name, exc,
            )
            extraction_errors.append(f"{file_path.name}: {exc}")

    if not merged_payload and extraction_errors:
        raise ValueError(
            f"All input files failed to parse: {'; '.join(extraction_errors)}"
        )

    if extraction_errors:
        logger.warning(
            "Shared folder read partial: agent=%s parsed_keys=%s errors=%s",
            agent_id, list(merged_payload.keys()), extraction_errors,
        )

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
    output_filename: Optional[str] = None,
) -> Path:
    """Write the agent result to the output folder in the requested format only.

    Writes exactly ONE file — the format requested via ``output_format``.
    Returns the path of the written file.
    """
    folder = resolve_output_folder(base_path, thread_id, output_subfolder)
    stem = (output_filename or f"{agent_id}_output").strip() or f"{agent_id}_output"

    if output_format == "json":
        out_path = folder / f"{agent_id}_output_{thread_id}.json"
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        from core.format_handler import render_output
        rendered = render_output(
            result, output_format, agent_id=agent_id, run_id=thread_id
        )
        out_path = folder / f"{stem}.{output_format}"
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
