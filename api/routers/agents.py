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
always reads input from ``{shared_folder.base_path}/{thread_id}/{input_subfolder}/``
and writes output to ``{shared_folder.base_path}/{thread_id}/{output_subfolder}/``.
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
    render_output,
)
from core.shared_folder import read_inputs, write_output

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
        input_subfolder = shared_io.get("input_subfolder", "")
        output_subfolder = shared_io.get("output_subfolder", "")

        if not base_path or not input_subfolder:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "server_misconfigured",
                    "message": "shared_folder.base_path and shared_io.input_subfolder must be configured",
                },
            )

        try:
            from core.llm_client import LLMClient
            llm_client = LLMClient()
            llm_config = cfg.llm_config(entry.agent_id)
            payload = await read_inputs(
                base_path=base_path,
                thread_id=x_thread_id,
                input_subfolder=input_subfolder,
                agent_id=entry.agent_id,
                llm_client=llm_client,
                llm_config=llm_config,
            )
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
                )
            except OSError as exc:
                logger.warning(
                    "Could not write to shared folder: agent=%s thread=%s err=%s",
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
