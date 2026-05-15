"""ADLC config loader (v2.0.0 — standalone agents).

Reads ``ADLC_Tech_Stack_Config.json`` first, then loads every agent config
listed under ``config_loader.files``. Per v2.0.0 the loader is intentionally
flat — there is no orchestrator config to merge against because GenWiz owns
orchestration. Each agent config stands alone.

LLM config for an agent is the merge of two layers (last writer wins):
1. ``ADLC_Tech_Stack_Config.json#llm`` — system-wide defaults
   (plus ``llm_defaults`` for behaviour knobs like retry_attempts)
2. ``<agent>_Config.json#llm_config_override`` — agent-specific overrides

Keys with ``None`` values or names starting with ``_`` are skipped at every
layer. Use :func:`get_config` for the module-level singleton.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.exceptions import ConfigLoadError


_TECH_STACK_ID = "ADLC-TECH"
_TECH_STACK_FILENAME = "ADLC_Tech_Stack_Config.json"
_FILES_KEY = "config_loader"
_LLM_KEY = "llm"
_LLM_DEFAULTS_KEY = "llm_defaults"
_LLM_OVERRIDE_KEY = "llm_config_override"
_CONFIG_DIR_ENV = "CONFIG_DIR"


class ADLCConfig:
    """In-memory view of the tech stack plus every agent config."""

    def __init__(self, configs_dir: Optional[Path] = None) -> None:
        self._configs_dir: Path = _resolve_configs_dir(configs_dir)
        self._tech: Dict[str, Any] = self._load_file(_TECH_STACK_FILENAME)

        files_block = self._tech.get(_FILES_KEY) or {}
        files_map = files_block.get("files") or {}
        if not isinstance(files_map, dict) or not files_map:
            raise ConfigLoadError(
                f"{_TECH_STACK_FILENAME} missing "
                f"'{_FILES_KEY}.files' map",
                detail={"path": str(self._configs_dir / _TECH_STACK_FILENAME)},
            )

        self._agents: Dict[str, Dict[str, Any]] = {}
        for entry_id, filename in files_map.items():
            if entry_id == _TECH_STACK_ID:
                continue
            if not isinstance(filename, str) or not filename.strip():
                raise ConfigLoadError(
                    f"config_loader.files['{entry_id}'] must be a non-empty "
                    f"filename string",
                    detail={"entry_id": entry_id, "value": filename},
                )
            self._agents[entry_id] = self._load_file(filename)

    # ---------------------------------------------------------------- public
    @property
    def configs_dir(self) -> Path:
        return self._configs_dir

    @property
    def tech_stack(self) -> Dict[str, Any]:
        """Raw tech stack config dict."""
        return self._tech

    @property
    def agent_ids(self) -> List[str]:
        return sorted(self._agents.keys())

    def agent_config(self, agent_id: str) -> Dict[str, Any]:
        """Raw agent config dict for ``agent_id``."""
        return self._require_agent(agent_id)

    def behaviour(self, agent_id: str) -> Dict[str, Any]:
        """The ``behaviour`` block from the agent config."""
        return self._require_agent(agent_id).get("behaviour", {}) or {}

    def inputs(self, agent_id: str) -> Dict[str, Any]:
        return self._require_agent(agent_id).get("inputs", {}) or {}

    def outputs(self, agent_id: str) -> Dict[str, Any]:
        return self._require_agent(agent_id).get("outputs", {}) or {}

    def manifest(self, agent_id: str) -> Dict[str, Any]:
        return self._require_agent(agent_id).get("manifest", {}) or {}

    def git_reader_config(self, agent_id: str) -> Dict[str, Any]:
        """Per-agent ``git_reader`` block (enabled flag, paths, branch, ...)."""
        return self._require_agent(agent_id).get("git_reader", {}) or {}

    def shared_folder_config(self) -> Dict[str, Any]:
        """System-wide ``shared_folder`` block from tech stack config.

        The ``base_path`` can be overridden by the ``SHARED_FOLDER_PATH``
        environment variable (useful for Docker where the mount point
        differs from the host path).
        """
        cfg = dict(self._tech.get("shared_folder", {}) or {})
        import os
        env_path = os.environ.get("SHARED_FOLDER_PATH")
        if env_path:
            cfg["base_path"] = env_path
        return cfg

    def shared_io_config(self, agent_id: str) -> Dict[str, Any]:
        """Per-agent ``shared_io`` block (input_subfolder, output_subfolder)."""
        return self._require_agent(agent_id).get("shared_io", {}) or {}

    def tech_git_reader(self) -> Dict[str, Any]:
        """Tech-stack ``git_reader`` block (local_audit_dir, cache_dir, ...)."""
        return self._tech.get("git_reader", {}) or {}

    def project_root(self) -> Path:
        """Repo root — one level above ``configs/``."""
        return self._configs_dir.parent

    def skill_file(self, agent_id: str) -> str:
        """Return the SKILL.md filename declared in the agent config."""
        agent_block = self._require_agent(agent_id).get("agent") or {}
        skill_file = agent_block.get("skill_file")
        if not isinstance(skill_file, str) or not skill_file.strip():
            raise ConfigLoadError(
                f"Agent '{agent_id}' config missing 'agent.skill_file'",
                detail={"agent_id": agent_id},
            )
        return skill_file

    def endpoint(self, agent_id: str) -> str:
        """HTTP endpoint path declared in the agent config."""
        agent_block = self._require_agent(agent_id).get("agent") or {}
        endpoint = agent_block.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.startswith("/"):
            raise ConfigLoadError(
                f"Agent '{agent_id}' config missing valid 'agent.endpoint'",
                detail={"agent_id": agent_id, "endpoint": endpoint},
            )
        return endpoint

    def mcp_tool_name(self, agent_id: str) -> Optional[str]:
        agent_block = self._require_agent(agent_id).get("agent") or {}
        name = agent_block.get("mcp_tool_name")
        return name if isinstance(name, str) and name.strip() else None

    def llm_config(self, agent_id: str) -> Dict[str, Any]:
        """Merge tech-stack LLM defaults with the agent's override block."""
        merged, _ = self._merge_llm(agent_id)
        return merged

    def explain_llm_config(self, agent_id: str) -> Dict[str, Any]:
        """Return merged config + per-key source map — useful for debugging."""
        merged, sources = self._merge_llm(agent_id)
        return {
            "agent_id": agent_id,
            "merged": merged,
            "sources": sources,
            "layers": [
                {
                    "name": _TECH_STACK_ID,
                    "block": _LLM_KEY,
                    "values": self._tech.get(_LLM_KEY, {}) or {},
                },
                {
                    "name": _TECH_STACK_ID,
                    "block": _LLM_DEFAULTS_KEY,
                    "values": self._tech.get(_LLM_DEFAULTS_KEY, {}) or {},
                },
                {
                    "name": agent_id,
                    "block": _LLM_OVERRIDE_KEY,
                    "values": self._require_agent(agent_id).get(
                        _LLM_OVERRIDE_KEY, {}
                    ) or {},
                },
            ],
        }

    # --------------------------------------------------------------- internal
    def _merge_llm(
        self, agent_id: str
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        layers: List[Tuple[str, Mapping[str, Any]]] = [
            (_TECH_STACK_ID, self._tech.get(_LLM_KEY, {}) or {}),
            (_TECH_STACK_ID, self._tech.get(_LLM_DEFAULTS_KEY, {}) or {}),
            (
                agent_id,
                self._require_agent(agent_id).get(_LLM_OVERRIDE_KEY, {}) or {},
            ),
        ]

        merged: Dict[str, Any] = {}
        sources: Dict[str, str] = {}
        for layer_name, layer in layers:
            for key, value in layer.items():
                if key.startswith("_") or value is None:
                    continue
                merged[key] = value
                sources[key] = layer_name

        # Normalise model key — tech stack uses ``default_model`` but the LLM
        # client expects ``model`` plus ``fallback_model``.
        if "default_model" in merged and "model" not in merged:
            merged["model"] = merged.pop("default_model")
        return merged, sources

    def _require_agent(self, agent_id: str) -> Dict[str, Any]:
        cfg = self._agents.get(agent_id)
        if cfg is None:
            raise ConfigLoadError(
                f"Unknown agent '{agent_id}'",
                detail={
                    "agent_id": agent_id,
                    "known_agents": sorted(self._agents.keys()),
                },
            )
        return cfg

    def _load_file(self, filename: str) -> Dict[str, Any]:
        path = self._configs_dir / filename
        if not path.is_file():
            raise ConfigLoadError(
                f"Config file not found: {filename}",
                detail={"path": str(path)},
            )
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ConfigLoadError(
                f"Invalid JSON in {filename}: {exc.msg}",
                detail={
                    "path": str(path),
                    "line": exc.lineno,
                    "column": exc.colno,
                },
            ) from exc
        if not isinstance(data, dict):
            raise ConfigLoadError(
                f"{filename} must be a JSON object at the top level",
                detail={"path": str(path), "type": type(data).__name__},
            )
        return data


def _resolve_configs_dir(explicit: Optional[Path]) -> Path:
    if explicit is not None:
        path = Path(explicit).resolve()
        if not path.is_dir():
            raise ConfigLoadError(
                f"Configured configs_dir does not exist: {path}",
                detail={"configured": str(explicit)},
            )
        return path

    env_override = os.environ.get(_CONFIG_DIR_ENV)
    if env_override:
        path = Path(env_override).resolve()
        if not path.is_dir():
            raise ConfigLoadError(
                f"{_CONFIG_DIR_ENV} points to a missing directory: {path}",
                detail={"env": _CONFIG_DIR_ENV, "value": env_override},
            )
        return path

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "configs"
        if candidate.is_dir():
            return candidate
    raise ConfigLoadError(
        f"Could not locate configs/ directory above {here}",
        detail={"start": str(here)},
    )


_instance: Optional[ADLCConfig] = None


def get_config(configs_dir: Optional[Path] = None) -> ADLCConfig:
    """Return the module-level :class:`ADLCConfig` singleton.

    Constructed lazily on first call. ``configs_dir`` is honoured only on
    that first call; subsequent calls return the existing instance.
    """
    global _instance
    if _instance is None:
        _instance = ADLCConfig(configs_dir=configs_dir)
    return _instance


def reset_config() -> None:
    """Reset the singleton — for tests only."""
    global _instance
    _instance = None
