"""Async Vertex Gemini LLM client.

Wraps :class:`google.genai.Client` (Vertex backend) with our retry policy:

1. Try the primary ``model`` from the merged ``llm_config``.
2. Retry up to ``retry_attempts`` times on timeouts and 429/5xx errors
   with exponential backoff.
3. If all retries on the primary are exhausted, repeat the same loop on
   ``fallback_model``.
4. On final failure, raise :class:`LLMCallError`.

Project, region, and credentials are read from the standard Vertex env
vars: ``GOOGLE_CLOUD_PROJECT``, ``GOOGLE_CLOUD_LOCATION``, and
``GOOGLE_APPLICATION_CREDENTIALS`` (or default application credentials).

System prompt and user-message contents are never written to logs or error
detail payloads — only metadata (agent_id, model, attempt counters, error
type) leaves this module.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Type

import httpx
from google import genai
from google.genai import types as genai_types
from google.genai.errors import APIError, ClientError, ServerError

from core.exceptions import LLMCallError

logger = logging.getLogger(__name__)


_TRANSIENT_BASE_ERRORS: Tuple[Type[BaseException], ...] = (
    asyncio.TimeoutError,
    ServerError,
)
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 30.0


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT_BASE_ERRORS):
        return True
    if isinstance(exc, ClientError) and getattr(exc, "code", None) == 429:
        return True
    return False


class LLMClient:
    """Thin async wrapper around the Vertex Gemini generate_content API."""

    def __init__(
        self,
        project: Optional[str] = None,
        location: Optional[str] = None,
        verify_ssl: bool = True,
    ) -> None:
        # project / location default to GOOGLE_CLOUD_PROJECT and
        # GOOGLE_CLOUD_LOCATION when None. Retry policy lives in this class,
        # so SDK-level retries stay at their default (we control attempts via
        # retry_attempts in the merged llm_config).
        
        # Check environment variable for SSL verification override
        if os.getenv("DISABLE_SSL_VERIFY", "").lower() in ("true", "1", "yes"):
            verify_ssl = False
            logger.warning(
                "SSL verification DISABLED via DISABLE_SSL_VERIFY env variable. "
                "This should only be used in development/corporate networks."
            )
        
        # Configure httpx client with SSL settings
        http_client = None
        if not verify_ssl:
            http_client = httpx.AsyncClient(verify=False)
            logger.warning("SSL certificate verification is DISABLED")
        
        # Create genai client with custom http_client
        if http_client:
            # Pass httpx client via api_client parameter
            self._client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
                http_options=httpx.AsyncClient(verify=False),
            )
        else:
            self._client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
            )

    async def call(
        self,
        *,
        system_prompt: str,
        user_message: str,
        config: Dict[str, Any],
        agent_id: str = "unknown",
    ) -> str:
        """Send a single user message under ``system_prompt`` and return text.

        ``config`` is the merged ``llm_config`` for the calling agent. It
        must contain ``model`` and may contain ``fallback_model``,
        ``max_tokens``, ``timeout_seconds``, and ``retry_attempts``. All
        runtime values are read from ``config`` — none are hardcoded here.
        """
        primary_model = config.get("model")
        if not primary_model:
            raise LLMCallError(
                "LLM config missing required 'model'",
                detail={"agent_id": agent_id},
            )
        fallback_model = config.get("fallback_model")
        retry_attempts = int(config.get("retry_attempts", 0))
        max_tokens = int(config.get("max_tokens", 1024))
        timeout = float(config.get("timeout_seconds", 60))
        response_mime_type = (
            "application/json"
            if str(config.get("response_format", "")).lower() == "json"
            else None
        )

        try:
            return await self._call_with_retry(
                model=primary_model,
                system_prompt=system_prompt,
                user_message=user_message,
                max_tokens=max_tokens,
                timeout=timeout,
                retry_attempts=retry_attempts,
                response_mime_type=response_mime_type,
                agent_id=agent_id,
            )
        except LLMCallError as primary_err:
            if not fallback_model:
                raise

            logger.warning(
                "LLM primary model exhausted, trying fallback: "
                "agent=%s primary=%s fallback=%s",
                agent_id, primary_model, fallback_model,
            )
            try:
                return await self._call_with_retry(
                    model=fallback_model,
                    system_prompt=system_prompt,
                    user_message=user_message,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    retry_attempts=retry_attempts,
                    response_mime_type=response_mime_type,
                    agent_id=agent_id,
                )
            except LLMCallError as fallback_err:
                raise LLMCallError(
                    f"LLM call failed on both primary and fallback for "
                    f"{agent_id}",
                    detail={
                        "agent_id": agent_id,
                        "primary_model": primary_model,
                        "fallback_model": fallback_model,
                        "primary": primary_err.detail,
                        "fallback": fallback_err.detail,
                    },
                ) from fallback_err

    async def _call_with_retry(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        max_tokens: int,
        timeout: float,
        retry_attempts: int,
        response_mime_type: Optional[str],
        agent_id: str,
    ) -> str:
        attempts_allowed = max(retry_attempts, 0) + 1
        last_error: Optional[BaseException] = None
        generation_config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            response_mime_type=response_mime_type,
        )

        for attempt in range(1, attempts_allowed + 1):
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=model,
                        contents=user_message,
                        config=generation_config,
                    ),
                    timeout=timeout,
                )
            except Exception as exc:
                if _is_transient(exc):
                    last_error = exc
                    logger.warning(
                        "LLM transient error: agent=%s model=%s attempt=%s/%s "
                        "type=%s",
                        agent_id, model, attempt, attempts_allowed,
                        type(exc).__name__,
                    )
                    if attempt < attempts_allowed:
                        delay = min(
                            _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                            _BACKOFF_MAX_SECONDS,
                        )
                        await asyncio.sleep(delay)
                    continue
                if isinstance(exc, APIError):
                    raise LLMCallError(
                        f"LLM API error: {type(exc).__name__}",
                        detail={
                            "agent_id": agent_id,
                            "model": model,
                            "error_type": type(exc).__name__,
                        },
                    ) from exc
                raise

            _check_finish_reason(
                response,
                agent_id=agent_id,
                model=model,
                max_tokens=max_tokens,
            )
            text = _extract_text(response)
            _maybe_capture(agent_id, text, response)
            return text

        raise LLMCallError(
            f"LLM exhausted {attempts_allowed} attempts on {model}",
            detail={
                "agent_id": agent_id,
                "model": model,
                "attempts": attempts_allowed,
                "error_type": (
                    type(last_error).__name__ if last_error else None
                ),
            },
        ) from last_error


def _maybe_capture(agent_id: str, text: str, response: Any) -> None:
    """If ``LLM_CAPTURE_DIR`` env var is set, dump raw LLM text + metadata.

    Used to debug parser/schema failures without re-running live LLM calls.
    Writes one file per LLM call: ``<agent_id>__<UTC>.txt`` containing the
    raw text. A sibling ``.meta.json`` records finish_reason and model.
    Quiet on errors — capture is a developer aid, not part of the contract.
    """
    capture_dir = os.environ.get("LLM_CAPTURE_DIR")
    if not capture_dir:
        return
    try:
        out_dir = Path(capture_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        safe_agent = agent_id.replace("/", "_").replace(":", "_")
        base = out_dir / f"{safe_agent}__{stamp}"
        base.with_suffix(".txt").write_text(text or "", encoding="utf-8")
        finish_reasons = []
        for cand in getattr(response, "candidates", None) or []:
            fr = getattr(cand, "finish_reason", None)
            finish_reasons.append(getattr(fr, "name", None) or str(fr))
        meta = {
            "agent_id": agent_id,
            "captured_at_utc": stamp,
            "chars": len(text or ""),
            "finish_reasons": finish_reasons,
        }
        import json as _json
        base.with_suffix(".meta.json").write_text(
            _json.dumps(meta, indent=2), encoding="utf-8"
        )
        logger.info(
            "LLM capture: agent=%s file=%s chars=%d",
            agent_id, base.with_suffix(".txt"), len(text or ""),
        )
    except Exception as exc:
        logger.warning("LLM capture failed (ignored): %s", exc)


def _check_finish_reason(
    response: Any,
    *,
    agent_id: str,
    model: str,
    max_tokens: int,
) -> None:
    """Raise a clear ``LLMCallError`` when Gemini truncated the output.

    Without this, a ``MAX_TOKENS`` finish silently returns malformed JSON
    and the downstream parser fails with a misleading delimiter error
    deep inside the truncated payload.
    """
    for cand in getattr(response, "candidates", None) or []:
        reason = getattr(cand, "finish_reason", None)
        reason_name = getattr(reason, "name", None) or str(reason) if reason else None
        if reason_name and reason_name.upper() == "MAX_TOKENS":
            raise LLMCallError(
                f"LLM output truncated at max_tokens={max_tokens} "
                f"(finish_reason=MAX_TOKENS). Increase llm_config_override.max_tokens "
                f"or reduce the size of the input payload.",
                detail={
                    "agent_id": agent_id,
                    "model": model,
                    "max_tokens": max_tokens,
                    "finish_reason": "MAX_TOKENS",
                },
            )


def _extract_text(response: Any) -> str:
    """Concatenate text from every text part in a Gemini response."""
    text = getattr(response, "text", None)
    if text is not None:
        return text
    parts: list[str] = []
    for cand in getattr(response, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            t = getattr(part, "text", None)
            if t is not None:
                parts.append(t)
    return "".join(parts)
