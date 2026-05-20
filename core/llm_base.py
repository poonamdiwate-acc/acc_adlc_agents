"""Abstract base for LLM clients.

Both :class:`LLMClient` (standard/direct SDK) and :class:`LiteLLMClient`
implement this interface so the rest of the codebase can depend on the
abstract contract without caring which backend is active.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseLLMClient(ABC):
    """Minimal async LLM call interface."""

    @abstractmethod
    async def call(
        self,
        *,
        system_prompt: str,
        user_message: str,
        config: Dict[str, Any],
        agent_id: str = "unknown",
    ) -> str:
        """Send a prompt and return the model's text response.

        Parameters
        ----------
        system_prompt : str
            System-level instruction for the model.
        user_message : str
            The user/content message to send.
        config : dict
            Merged LLM config for the calling agent. Must contain ``model``
            and may contain ``fallback_model``, ``max_tokens``,
            ``timeout_seconds``, ``retry_attempts``.
        agent_id : str
            Identifier for logging/tracing.

        Returns
        -------
        str
            Raw text response from the model.

        Raises
        ------
        LLMCallError
            On exhausted retries or non-transient API errors.
        """
        ...
