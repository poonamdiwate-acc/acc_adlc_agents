"""Agent dispatch routes.

This is the HTTP face of :mod:`core.agent_registry`. We iterate every
registered ``AgentEntry`` once at import time and bind a POST handler at
its configured ``endpoint``. The actual routing is one lookup —
``registry.get_by_endpoint(...)`` — followed by an ``await`` on the
agent's handler.

The ``X-Run-ID`` header is required on every agent call (per
``ADLC_Tech_Stack_Config.json#api.run_id_endpoint``). It is propagated to
the agent handler and echoed in the response.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.agent_registry import AgentNotFound, get_registry

_bearer_scheme = HTTPBearer(description="Enter your ADLC_API_KEY token")
from core.config_loader import get_config
from core.exceptions import (
    ADLCError,
    LLMCallError,
    OutputParseError,
    PipelineStopError,
)

logger = logging.getLogger(__name__)

# Repo root: this file is api/routers/agents.py — two parents up.
_OUTPUTS_DIR = Path(__file__).resolve().parents[2] / "tests" / "outputs"


def _maybe_load_dev_fixture(agent_id: str) -> Optional[Dict[str, Any]]:
    """Return the dev phase_input fixture for ``agent_id`` or None.

    Returns None unless all of these are true:
        * ENV environment variable equals 'dev' (case-insensitive)
        * the agent config's ``dev.enabled`` is true
        * ``dev.phase_input_fixture`` resolves to a readable JSON object

    Any other case (no block, disabled, missing/invalid file) returns
    None and lets the normal validation flow surface the missing inputs.

    If ``dev.phase_input_text_files`` is declared, raw text files are
    loaded into the corresponding payload fields (overwriting empty values
    from the JSON fixture).
    """
    if os.environ.get("ENV", "").lower() != "dev":
        return None

    cfg = get_config()
    dev_block = cfg.dev_config(agent_id)
    if not dev_block.get("enabled"):
        return None

    fixture_path = dev_block.get("phase_input_fixture")
    if not isinstance(fixture_path, str) or not fixture_path.strip():
        return None

    full_path = (cfg.project_root() / fixture_path).resolve()
    if not full_path.is_file():
        logger.warning(
            "Dev fixture configured but file missing: agent=%s path=%s",
            agent_id, full_path,
        )
        return None

    try:
        content = json.loads(full_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(
            "Dev fixture unreadable: agent=%s path=%s err=%s",
            agent_id, full_path, exc,
        )
        return None

    if not isinstance(content, dict):
        logger.warning(
            "Dev fixture must be a JSON object: agent=%s path=%s type=%s",
            agent_id, full_path, type(content).__name__,
        )
        return None

    text_files = dev_block.get("phase_input_text_files")
    if isinstance(text_files, dict):
        for field_name, rel_path in text_files.items():
            if not isinstance(rel_path, str) or not rel_path.strip():
                continue
            text_path = (cfg.project_root() / rel_path).resolve()
            if text_path.is_file():
                try:
                    content[field_name] = text_path.read_text(encoding="utf-8")
                    logger.info(
                        "Dev text file loaded: agent=%s field=%s path=%s",
                        agent_id, field_name, text_path,
                    )
                except OSError as exc:
                    logger.warning(
                        "Dev text file unreadable: agent=%s field=%s err=%s",
                        agent_id, field_name, exc,
                    )
            else:
                logger.warning(
                    "Dev text file missing: agent=%s field=%s path=%s",
                    agent_id, field_name, text_path,
                )

    logger.info(
        "Dev fixture loaded for empty body: agent=%s path=%s",
        agent_id, full_path,
    )
    return content


def _persist_output(agent_id: str, run_id: str, result: Dict[str, Any]) -> None:
    """Best-effort write of the agent response to tests/outputs/.

    Path: tests/outputs/{run_id}/{agent_id}_output.json. Filesystem errors
    are logged but never propagated — the HTTP response is the source of
    truth; this is a dev convenience until git_writer.py exists.
    """
    try:
        out_dir = _OUTPUTS_DIR / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{agent_id}_output.json"
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Persisted agent output: %s", out_path)
    except OSError as exc:
        logger.warning(
            "Could not persist agent output: agent=%s run_id=%s err=%s",
            agent_id, run_id, exc,
        )

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
        _credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    ) -> Dict[str, Any]:
        try:
            entry = get_registry().get_by_endpoint(endpoint)
        except AgentNotFound as exc:
            raise HTTPException(
                status_code=404, detail={"error": exc.message, **exc.detail}
            ) from exc

        body_bytes = await request.body()
        payload: Dict[str, Any]
        if body_bytes.strip():
            try:
                parsed = json.loads(body_bytes)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "invalid_json", "message": str(exc)},
                ) from exc
            if not isinstance(parsed, dict):
                raise HTTPException(
                    status_code=400,
                    detail={"error": "payload_must_be_object"},
                )
            payload = parsed
        else:
            dev_payload = _maybe_load_dev_fixture(entry.agent_id)
            if dev_payload is None:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "empty_body"},
                )
            payload = dev_payload

        logger.info(
            "Dispatch: endpoint=%s agent=%s run_id=%s",
            endpoint, entry.agent_id, x_run_id,
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

        _persist_output(entry.agent_id, x_run_id, result)
        return result

    return handler


_register_routes()
