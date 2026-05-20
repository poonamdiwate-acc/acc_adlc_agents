"""
LiteLLM Factory — Unified LLM interface for LangChain agents.
Supports Direct SDK mode and Proxy Gateway mode.

Usage:
    from litellm_factory import LiteLLMFactory

    # From config.ini (simplest)
    llm = LiteLLMFactory.create_from_config_ini("llm_config.ini", section="azure")

    # Use like any LangChain ChatModel
    response = llm.invoke("Hello, world!")
"""

from typing import Optional, Dict, Any, List
import os
import logging
from configparser import ConfigParser
from pathlib import Path

import litellm
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    BaseMessage, AIMessage, HumanMessage, SystemMessage, ToolMessage
)
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.callbacks import (
    CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
)
from pydantic import Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load global settings from llm_config.ini
# ---------------------------------------------------------------------------
_config = ConfigParser()
_config_path = Path(__file__).parent / "llm_config.ini"

USE_PROXY = False
PROXY_URL = ""
PROXY_KEY = ""
PROXY_MODEL = ""
DEFAULT_TIMEOUT = 600.0
NUM_RETRIES = 3

if _config_path.exists():
    _config.read(_config_path)
    USE_PROXY = _config.getboolean("litellm_gateway", "use_proxy", fallback=False)
    PROXY_URL = _config.get("litellm_gateway", "proxy_url", fallback="")
    PROXY_KEY = _config.get("litellm_gateway", "key", fallback="")
    PROXY_MODEL = _config.get("litellm_gateway", "model", fallback="")
    DEFAULT_TIMEOUT = _config.getfloat("appsettings", "timeout_seconds", fallback=600)
    NUM_RETRIES = _config.getint("appsettings", "num_retries", fallback=3)


# ---------------------------------------------------------------------------
# Cost tracking callback
# ---------------------------------------------------------------------------
def _track_cost(kwargs, completion_response, start_time, end_time):
    try:
        cost = getattr(completion_response, '_hidden_params', {}).get('response_cost', 0)
        model = kwargs.get('model', 'unknown')
        duration = (end_time - start_time).total_seconds()
        logger.info(f"LLM Call | model={model} | cost=${cost:.6f} | duration={duration:.2f}s")
    except Exception as e:
        logger.debug(f"Cost tracking error: {e}")


def configure_litellm(
    num_retries: int = NUM_RETRIES,
    timeout_seconds: float = DEFAULT_TIMEOUT,
    enable_cost_tracking: bool = True,
):
    """Initialize LiteLLM global settings."""
    litellm.num_retries = num_retries
    litellm.request_timeout = timeout_seconds
    litellm.suppress_debug_info = True

    callbacks = []
    if enable_cost_tracking:
        callbacks.append(_track_cost)
    litellm.callbacks = callbacks


# Auto-initialize on import
configure_litellm()


# ---------------------------------------------------------------------------
# LiteLLMChat — LangChain-compatible ChatModel
# ---------------------------------------------------------------------------
class LiteLLMChat(BaseChatModel):
    """LangChain ChatModel backed by litellm.completion()."""

    model: str = Field(description="LiteLLM model string, e.g. 'azure/gpt-4.1'")
    temperature: float = Field(default=0.7)
    max_tokens: Optional[int] = Field(default=None)
    api_key: Optional[str] = Field(default=None)
    api_base: Optional[str] = Field(default=None)
    api_version: Optional[str] = Field(default=None)
    fallbacks: Optional[List[str]] = Field(default=None)
    num_retries: Optional[int] = Field(default=None)
    timeout: Optional[float] = Field(default=None)
    extra_params: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True

    @property
    def _llm_type(self) -> str:
        return "litellm"

    # --- Message conversion ---
    def _to_dict(self, msg: BaseMessage) -> Dict[str, Any]:
        if isinstance(msg, HumanMessage):
            return {"role": "user", "content": msg.content}
        elif isinstance(msg, AIMessage):
            d = {"role": "assistant", "content": msg.content}
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                import json as _json
                d["tool_calls"] = [
                    {
                        "id": tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", ""),
                            "arguments": _json.dumps(tc.get("args", {})) if isinstance(tc, dict) else _json.dumps(getattr(tc, "args", {})),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            return d
        elif isinstance(msg, SystemMessage):
            return {"role": "system", "content": msg.content}
        elif isinstance(msg, ToolMessage):
            return {"role": "tool", "content": msg.content, "tool_call_id": msg.tool_call_id, "name": msg.name}
        return {"role": "user", "content": str(msg.content)}

    # --- Build params ---
    def _build_params(self, **kwargs) -> Dict[str, Any]:
        params = {"model": self.model, "temperature": kwargs.pop("temperature", self.temperature)}
        if self.api_base:
            params["api_base"] = self.api_base
        if self.api_key:
            params["api_key"] = self.api_key
        if self.max_tokens:
            params["max_tokens"] = kwargs.pop("max_tokens", self.max_tokens)
        if self.api_version:
            params["api_version"] = self.api_version
        if self.fallbacks:
            params["fallbacks"] = self.fallbacks
        if self.num_retries is not None:
            params["num_retries"] = self.num_retries
        if self.timeout is not None:
            params["timeout"] = self.timeout
        params.update(self.extra_params)
        params.update(kwargs)
        return params

    # --- Sync generation ---
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        params = self._build_params(**kwargs)
        params["messages"] = [self._to_dict(m) for m in messages]
        if stop:
            params["stop"] = stop

        response = litellm.completion(**params)
        return self._parse_response(response)

    # --- Async generation ---
    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        params = self._build_params(**kwargs)
        params["messages"] = [self._to_dict(m) for m in messages]
        if stop:
            params["stop"] = stop

        response = await litellm.acompletion(**params)
        return self._parse_response(response)

    # --- Parse response ---
    def _parse_response(self, response) -> ChatResult:
        import json as _json
        choice = response.choices[0]
        content = choice.message.content or ""

        tool_calls = []
        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = _json.loads(args)
                    except Exception:
                        args = {}
                tool_calls.append({"name": tc.function.name, "args": args, "id": tc.id, "type": "tool_call"})

        ai_message = AIMessage(content=content, tool_calls=tool_calls)

        generation_info = {"finish_reason": choice.finish_reason, "model": response.model}
        if hasattr(response, "usage"):
            generation_info["token_usage"] = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return ChatResult(generations=[ChatGeneration(message=ai_message, generation_info=generation_info)])

    # --- Tool binding ---
    def bind_tools(self, tools: List[Any], **kwargs) -> "LiteLLMChat":
        from langchain_core.utils.function_calling import convert_to_openai_tool

        tool_dicts = [convert_to_openai_tool(t) for t in tools]
        extra = self.extra_params.copy()
        extra["tools"] = tool_dicts
        if "tool_choice" in kwargs:
            extra["tool_choice"] = kwargs.pop("tool_choice")
        extra.update(kwargs)

        return LiteLLMChat(
            model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
            api_key=self.api_key, api_base=self.api_base, api_version=self.api_version,
            fallbacks=self.fallbacks, num_retries=self.num_retries, timeout=self.timeout,
            extra_params=extra,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
class LiteLLMFactory:
    """Factory to create LiteLLMChat from various config sources."""

    @staticmethod
    def create_from_config_ini(
        config_path: str = str(_config_path),
        section: str = "azure",
        temperature: Optional[float] = None,
        fallbacks: Optional[List[str]] = None,
        num_retries: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> LiteLLMChat:
        """
        Create LiteLLMChat from a config.ini file.

        Args:
            config_path: Path to ini file (default: llm_config.ini next to this file)
            section: Section name — 'azure', 'bedrock', or 'vertex'
            temperature: Override temperature
            fallbacks: Fallback model list
            num_retries: Retry count
            timeout: Request timeout (seconds)
        """
        config = ConfigParser()
        config.read(config_path)

        # --- Proxy mode ---
        if USE_PROXY:
            logger.info(f"Routing via LiteLLM Proxy: {PROXY_URL}")
            proxy_model = config.get(section, "model", fallback=PROXY_MODEL)
            temp = temperature if temperature is not None else config.getfloat(section, "temperature", fallback=0.7)

            proxy_api_key = PROXY_KEY or os.getenv("LITELLM_PROXY_API_KEY", "")
            if not proxy_api_key:
                raise ValueError("Proxy key not set in llm_config.ini or LITELLM_PROXY_API_KEY env var")

            return LiteLLMChat(
                model=proxy_model,
                temperature=temp,
                api_key=proxy_api_key,
                api_base=PROXY_URL,
                fallbacks=fallbacks,
                num_retries=num_retries,
                timeout=timeout,
            )

        # --- Direct SDK mode ---
        if section == "azure":
            api_key = config.get(section, "api_key")
            endpoint = config.get(section, "endpoint")
            api_version = config.get(section, "api_version")
            deployment_name = config.get(section, "deployment_name")
            temp = temperature if temperature is not None else config.getfloat(section, "temperature", fallback=0.7)

            return LiteLLMChat(
                model=f"azure/{deployment_name}",
                temperature=temp,
                api_key=api_key,
                api_base=endpoint,
                api_version=api_version,
                fallbacks=fallbacks,
                num_retries=num_retries,
                timeout=timeout,
            )

        elif section == "bedrock":
            model_id = config.get(section, "model_id")
            region = config.get(section, "region", fallback="us-east-1")
            aws_key = config.get(section, "aws_access_key_id", fallback="")
            aws_secret = config.get(section, "aws_secret_access_key", fallback="")

            extra = {}
            if aws_key:
                extra["aws_access_key_id"] = aws_key
            if aws_secret:
                extra["aws_secret_access_key"] = aws_secret
            if region:
                extra["aws_region_name"] = region

            return LiteLLMChat(
                model=f"bedrock/{model_id}",
                temperature=temperature or 0.7,
                fallbacks=fallbacks,
                num_retries=num_retries,
                timeout=timeout,
                extra_params=extra,
            )

        elif section == "vertex":
            model_name = config.get(section, "model_name")
            project_id = config.get(section, "project_id")
            location = config.get(section, "location", fallback="us-central1")

            return LiteLLMChat(
                model=f"vertex_ai/{model_name}",
                temperature=temperature or 0.7,
                fallbacks=fallbacks,
                num_retries=num_retries,
                timeout=timeout,
                extra_params={"vertex_project": project_id, "vertex_location": location},
            )

        else:
            raise ValueError(f"Unknown section: {section}. Use 'azure', 'bedrock', or 'vertex'.")

    @staticmethod
    def create_direct(
        model: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        api_version: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LiteLLMChat:
        """
        Create LiteLLMChat with explicit parameters (no config file needed).

        Args:
            model: LiteLLM model string, e.g. 'azure/gpt-4.1', 'bedrock/claude-v2'
            api_key: API key
            api_base: Base URL
            api_version: API version (Azure)
            temperature: Generation temperature
            max_tokens: Max output tokens
        """
        return LiteLLMChat(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            api_base=api_base,
            api_version=api_version,
            extra_params=kwargs,
        )
