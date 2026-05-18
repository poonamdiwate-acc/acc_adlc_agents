"""Agent dispatch routes.

This is the HTTP face of :mod:`core.agent_registry`. We iterate every
registered ``AgentEntry`` once at import time and bind a POST handler at
its configured ``endpoint``. The actual routing is one lookup —
``registry.get_by_endpoint(...)`` — followed by an ``await`` on the
agent's handler.

The ``X-Run-ID`` header is required on every agent call (per
``ADLC_Tech_Stack_Config.json#api.run_id_endpoint``). It is propagated to
the agent handler and echoed in the response.

The ``X-Thread-ID`` header identifies the shared folder thread. The agent
reads input from one or more subfolders within ``{shared_folder.base_path}/{thread_id}/``:
- Most agents use a single ``input_subfolder`` (e.g., ``bs_docs``)
- Some agents use multiple ``input_subfolders`` to read from different sources
  (e.g., DE-04 reads from both ``bs_docs`` and ``data_design_response``)
- Special filtering: ``data_design_response`` folder reads only JSON files
Outputs are written to ``{shared_folder.base_path}/{thread_id}/{output_subfolder}/``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response

from core.agent_registry import AgentNotFound, get_registry
from core.config_loader import get_config
from core.exceptions import (
    ADLCError,
    LLMCallError,
    OutputParseError,
    PipelineStopError,
)
from core.format_handler import (
    SUPPORTED_OUTPUT_FORMATS,
    parse_input,
    render_output,
)
from core.shared_folder import read_inputs, write_output, find_file_by_patterns

logger = logging.getLogger(__name__)

router = APIRouter()


def _register_routes() -> None:
    """Bind one POST handler per registered agent's endpoint."""
    registry = get_registry()
    for entry in registry.all_entries():
        router.add_api_route(
            entry.endpoint,
            _make_handler(entry.endpoint),
            methods=["POST"],
            name=f"agent_{entry.agent_id}",
            tags=["agents"],
        )


def _make_handler(endpoint: str):
    async def handler(
        request: Request,
        x_run_id: str = Header(..., alias="X-Run-ID"),
        x_thread_id: str = Header(..., alias="X-Thread-ID"),
        output_format: str = Query(default="json", alias="format"),
    ) -> Response:
        try:
            entry = get_registry().get_by_endpoint(endpoint)
        except AgentNotFound as exc:
            raise HTTPException(
                status_code=404, detail={"error": exc.message, **exc.detail}
            ) from exc

        # Validate requested output format
        if output_format.lower() not in SUPPORTED_OUTPUT_FORMATS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unsupported_output_format",
                    "format": output_format,
                    "supported": SUPPORTED_OUTPUT_FORMATS,
                },
            )

        # --- Read inputs from shared folder ---
        cfg = get_config()
        shared_cfg = cfg.shared_folder_config()
        shared_io = cfg.shared_io_config(entry.agent_id)

        base_path = shared_cfg.get("base_path", "")
        
        # Support NEW input_sources pattern (with file name search) or OLD input_subfolders pattern
        input_sources = shared_io.get("input_sources")
        
        if input_sources:
            # NEW pattern: input_sources with file_name_patterns support
            pass  # Will handle below
        else:
            # OLD pattern: input_subfolder or input_subfolders
            input_subfolder = shared_io.get("input_subfolder")
            input_subfolders = shared_io.get("input_subfolders")
            
            if input_subfolders:
                input_folders_list = input_subfolders if isinstance(input_subfolders, list) else [input_subfolders]
            elif input_subfolder:
                input_folders_list = [input_subfolder]
            else:
                input_folders_list = []
            
            # Convert to input_sources format for unified handling
            input_sources = [
                {"subfolder": subfolder, "allowed_extensions": [".json"] if subfolder == "data_design_response" else None}
                for subfolder in input_folders_list
            ]
            
        output_subfolder = shared_io.get("output_subfolder", "")
        output_filename = shared_io.get("output_filename") or None

        if not base_path or not input_sources:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "server_misconfigured",
                    "message": "shared_folder.base_path and shared_io input sources must be configured",
                },
            )

        try:
            from core.llm_client import LLMClient
            llm_client = LLMClient()
            llm_config = cfg.llm_config(entry.agent_id)
            
            # Read from all input sources and merge
            payload = {}
            for source in input_sources:
                subfolder = source.get("subfolder", "bs_docs")
                file_patterns = source.get("file_name_patterns")
                required = source.get("required", False)
                extensions = source.get("allowed_extensions")
                field_name = source.get("field_name")
                
                if file_patterns:
                    # Search for specific files by name
                    file_path = find_file_by_patterns(
                        base_path=base_path,
                        thread_id=x_thread_id,
                        subfolder=subfolder,
                        file_name_patterns=file_patterns,
                        allowed_extensions=extensions,
                    )
                    
                    if not file_path:
                        if required:
                            raise HTTPException(
                                status_code=400,
                                detail={
                                    "error": "required_file_missing",
                                    "message": f"Required file not found: {file_patterns} in {subfolder}",
                                    "patterns": file_patterns,
                                    "subfolder": subfolder,
                                },
                            )
                        else:
                            logger.info(
                                "Optional file not found: patterns=%s subfolder=%s thread=%s",
                                file_patterns, subfolder, x_thread_id
                            )
                            continue
                    
                    # Parse the found file
                    file_bytes = file_path.read_bytes()
                    # Map extension to content type
                    ext = file_path.suffix.lower()
                    content_type_map = {
                        ".json": "application/json",
                        ".html": "text/html",
                        ".htm": "text/html",
                        ".md": "text/markdown",
                        ".markdown": "text/markdown",
                    }
                    content_type = content_type_map.get(ext, "application/octet-stream")
                    
                    parsed = await parse_input(
                        content=file_bytes,
                        content_type=content_type,
                        filename=file_path.name,
                        agent_id=entry.agent_id,
                        llm_client=llm_client,
                        llm_config=llm_config,
                    )
                    
                    # Store with field name if specified, otherwise merge
                    if field_name:
                        # For diagrams, store the raw text content
                        if content_type in ["text/html", "text/markdown", "text/x-markdown"]:
                            payload[field_name] = parsed.get("raw_text") or str(parsed)
                        else:
                            payload[field_name] = parsed
                    else:
                        payload.update(parsed)
                else:
                    # Read all files from subfolder (old behavior)
                    folder_payload = await read_inputs(
                        base_path=base_path,
                        thread_id=x_thread_id,
                        input_subfolder=subfolder,
                        agent_id=entry.agent_id,
                        llm_client=llm_client,
                        llm_config=llm_config,
                        allowed_extensions=extensions,
                    )
                    payload.update(folder_payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "shared_folder_error", "message": str(exc)},
            ) from exc

        logger.info(
            "Dispatch: endpoint=%s agent=%s run_id=%s thread_id=%s format=%s",
            endpoint, entry.agent_id, x_run_id, x_thread_id, output_format,
        )

        try:
            result = await entry.handler(payload, x_run_id)
        except PipelineStopError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "pipeline_stop", "message": exc.message, **exc.detail},
            ) from exc
        except OutputParseError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "llm_output_invalid", "message": exc.message, **exc.detail},
            ) from exc
        except LLMCallError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "llm_call_failed", "message": exc.message, **exc.detail},
            ) from exc
        except ADLCError as exc:
            raise HTTPException(
                status_code=500,
                detail={"error": "adlc_error", "message": exc.message, **exc.detail},
            ) from exc

        # --- Write output to shared folder ---
        fmt = output_format.lower()
        if output_subfolder:
            try:
                write_output(
                    base_path=base_path,
                    thread_id=x_thread_id,
                    output_subfolder=output_subfolder,
                    agent_id=entry.agent_id,
                    result=result,
                    output_format=fmt,
                    output_filename=output_filename,
                )
            except OSError as exc:
                logger.warning(
                    "Could not write to shared folder: agent=%s thread=%s err=%s",
                    entry.agent_id, x_thread_id, exc,
                )
            # When JSON is requested, also write companion formats for human viewing
            # DE-04 (API Contracts): Write both JSON + DOCX
            # Other agents: Write JSON + HTML
            # Best-effort — never block the response on render/IO failures.
            if fmt == "json":
                companion_format = "docx" if entry.agent_id == "DE-04" else "html"
                try:
                    write_output(
                        base_path=base_path,
                        thread_id=x_thread_id,
                        output_subfolder=output_subfolder,
                        agent_id=entry.agent_id,
                        result=result,
                        output_format=companion_format,
                        output_filename=output_filename,
                    )
                except Exception as exc:
                    logger.info(
                        "%s companion not written (skipped): agent=%s "
                        "thread=%s err=%s",
                        companion_format.upper(), entry.agent_id, x_thread_id, exc,
                    )
            
            # DE-04: When DOCX is requested, also write JSON for downstream agents
            elif fmt == "docx" and entry.agent_id == "DE-04":
                try:
                    write_output(
                        base_path=base_path,
                        thread_id=x_thread_id,
                        output_subfolder=output_subfolder,
                        agent_id=entry.agent_id,
                        result=result,
                        output_format="json",
                        output_filename=output_filename,
                    )
                except Exception as exc:
                    logger.info(
                        "JSON companion not written (skipped): agent=%s "
                        "thread=%s err=%s",
                        entry.agent_id, x_thread_id, exc,
                    )

        # --- HTTP response ---
        if fmt == "json":
            return result  # type: ignore[return-value]

        try:
            rendered = render_output(
                result, fmt, agent_id=entry.agent_id, run_id=x_run_id
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "render_error", "message": str(exc)},
            ) from exc

        return Response(
            content=rendered.content,
            media_type=rendered.content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{rendered.filename}"',
            },
        )

    return handler


_register_routes()
