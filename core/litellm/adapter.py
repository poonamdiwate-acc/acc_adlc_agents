"""LiteLLM Adapter — bridges LiteLLMFactory/LiteLLMChat to BaseLLMClient.

When ``llm.mode`` is ``"litellm"``, the factory returns an instance of this
adapter. It wraps the LangChain-compatible ``LiteLLMChat`` from
``core.litellm.litellm_factory`` so it satisfies the same
``BaseLLMClient.call()`` interface that all agents depend on.

This keeps agent code completely unchanged regardless of which LLM mode
is active — agents always call ``await llm_client.call(...)``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.exceptions import LLMCallError
from core.llm_base import BaseLLMClient

logger = logging.getLogger(__name__)

# Map ADLC active_provider names to llm_config.ini section names
_PROVIDER_TO_SECTION = {
    "google-vertex": "vertex",
    "aws-bedrock": "bedrock",
    "azure-openai": "azure",
}


class LiteLLMAdapter(BaseLLMClient):
    """Adapts LiteLLMChat (LangChain interface) to BaseLLMClient.call().

    Instantiated by the factory with the appropriate LiteLLMChat instance
    pre-configured from ``llm_config.ini``.
    """

    def __init__(self, section: str = "azure") -> None:
        from core.litellm.litellm_factory import LiteLLMFactory

        self._section = section
        self._chat = LiteLLMFactory.create_from_config_ini(section=section)
        logger.info(
            "LiteLLMAdapter initialised: section=%s model=%s",
            section, self._chat.model,
        )

    async def call(
        self,
        *,
        system_prompt: str,
        user_message: str,
        config: Dict[str, Any],
        agent_id: str = "unknown",
    ) -> str:
        """Send a prompt via LiteLLM and return raw text response.

        Overrides from ``config`` (max_tokens, timeout, temperature) are
        applied per-call without mutating the underlying LiteLLMChat instance.
        """
        from langchain_core.messages import SystemMessage, HumanMessage

        max_tokens = config.get("max_tokens")
        timeout = config.get("timeout_seconds")
        temperature = config.get("temperature")

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]

        # Build per-call overrides
        kwargs: Dict[str, Any] = {}
        if max_tokens:
            kwargs["max_tokens"] = int(max_tokens)
        if timeout:
            kwargs["timeout"] = float(timeout)
        if temperature is not None:
            kwargs["temperature"] = float(temperature)

        try:
            result = await self._chat.ainvoke(messages, **kwargs)
            text = result.content if hasattr(result, "content") else str(result)
            return text
        except Exception as exc:
            raise LLMCallError(
                f"LiteLLM call failed for {agent_id}: {type(exc).__name__}",
                detail={
                    "agent_id": agent_id,
                    "section": self._section,
                    "model": self._chat.model,
                    "error_type": type(exc).__name__,
                },
            ) from exc
