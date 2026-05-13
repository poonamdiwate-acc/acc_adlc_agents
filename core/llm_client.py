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
from typing import Any, Dict, Optional, Tuple, Type

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
    ) -> None:
        # project / location default to GOOGLE_CLOUD_PROJECT and
        # GOOGLE_CLOUD_LOCATION when None. Retry policy lives in this class,
        # so SDK-level retries stay at their default (we control attempts via
        # retry_attempts in the merged llm_config).
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

            return _extract_text(response)

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
