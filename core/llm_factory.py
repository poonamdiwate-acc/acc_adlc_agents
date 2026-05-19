"""LLM client factory — returns the correct client based on config mode.

Reads ``llm.mode`` from the tech stack config:
- ``"standard"`` → :class:`LLMClient` (direct SDK per active_provider)
  Standard LLM is ON, LiteLLM is OFF.
- ``"litellm"``  → :class:`LiteLLMAdapter` (routes via core/litellm/)
  LiteLLM is ON, Standard LLM is OFF.

Only one mode can be active at a time — they are mutually exclusive.

When standard mode is active, the user selects the provider via
``llm.active_provider`` (google-vertex | aws-bedrock | azure-openai)
and optionally overrides the model via ``llm.active_model``.

Usage in agent code::

    from core.llm_factory import create_llm_client
    from core.config_loader import get_config

    cfg = get_config()
    llm_client = create_llm_client(cfg)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from core.llm_base import BaseLLMClient

if TYPE_CHECKING:
    from core.config_loader import ADLCConfig

logger = logging.getLogger(__name__)

# Map ADLC provider names → litellm ini sections
_PROVIDER_TO_SECTION = {
    "google-vertex": "vertex",
    "aws-bedrock": "bedrock",
    "azure-openai": "azure",
}


def create_llm_client(config: "ADLCConfig") -> BaseLLMClient:
    """Instantiate the appropriate LLM client based on tech-stack config.

    Parameters
    ----------
    config : ADLCConfig
        The loaded ADLC configuration instance.

    Returns
    -------
    BaseLLMClient
        Either an LLMClient (standard mode) or LiteLLMAdapter (litellm mode).

    Raises
    ------
    ValueError
        If mode is not 'standard' or 'litellm'.
    """
    llm_block = config.tech_stack.get("llm", {}) or {}
    mode = os.environ.get("LLM_MODE", llm_block.get("mode", "standard")).lower()
    active_provider = llm_block.get("active_provider", "google-vertex")

    if mode == "litellm":
        # LiteLLM ON — Standard OFF
        from core.litellm.adapter import LiteLLMAdapter

        section = _PROVIDER_TO_SECTION.get(active_provider, "azure")
        logger.info(
            "LLM factory: mode=litellm, routing via core/litellm/ (section=%s)",
            section,
        )
        return LiteLLMAdapter(section=section)

    elif mode == "standard":
        # Standard ON — LiteLLM OFF
        from core.llm_client import LLMClient

        # Allow user to override model via active_model config field
        active_model = llm_block.get("active_model")
        logger.info(
            "LLM factory: mode=standard, provider=%s, model_override=%s",
            active_provider, active_model,
        )
        return LLMClient(provider=active_provider)

    else:
        raise ValueError(
            f"Invalid llm.mode='{mode}'. Must be 'standard' or 'litellm'."
        )
