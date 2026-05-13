"""SKILL.md loader.

Loads the system prompt from a SKILL.md file by extracting the fenced code
block under the ``## System prompt`` heading. Results are cached in memory
to avoid repeated disk reads.

The skill directory is sourced from
``ADLC_Tech_Stack_Config.json#skill_loader.skill_dir`` — never hardcoded.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Optional

from core.exceptions import ConfigLoadError, SkillLoadError


_TECH_STACK_FILENAME = "ADLC_Tech_Stack_Config.json"
_SKILL_LOADER_BLOCK = "skill_loader"
_SKILL_DIR_KEY = "skill_dir"
_SKILL_DIR_ENV = "SKILL_DIR"
_SYSTEM_PROMPT_PATTERN = re.compile(
    r"##\s+System prompt\s*\n+```[^\n]*\n(.*?)\n```",
    flags=re.DOTALL,
)


_prompt_cache: Dict[str, str] = {}
_skill_dir: Optional[Path] = None


def load_system_prompt(skill_file: str) -> str:
    """Return the ``## System prompt`` fenced block from ``skill_file``.

    Cached on first read; subsequent calls return the cached string. Raises
    :class:`SkillLoadError` if the file does not exist or the section is
    missing / malformed.
    """
    cached = _prompt_cache.get(skill_file)
    if cached is not None:
        return cached

    skill_dir = _resolve_skill_dir()
    path = skill_dir / skill_file
    if not path.is_file():
        raise SkillLoadError(
            f"SKILL file not found: {skill_file}",
            detail={"path": str(path), "skill_dir": str(skill_dir)},
        )

    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = _SYSTEM_PROMPT_PATTERN.search(text)
    if not match:
        raise SkillLoadError(
            f"'## System prompt' fenced block not found in {skill_file}",
            detail={"path": str(path)},
        )

    prompt = match.group(1).rstrip() + "\n"
    _prompt_cache[skill_file] = prompt
    return prompt


def _resolve_skill_dir() -> Path:
    """Resolve and cache the skill directory.

    Precedence: ``$SKILL_DIR`` env override → ``skill_loader.skill_dir`` in
    ``ADLC_Tech_Stack_Config.json``.
    """
    global _skill_dir
    if _skill_dir is not None:
        return _skill_dir

    env_override = os.environ.get(_SKILL_DIR_ENV)
    if env_override:
        resolved = Path(env_override).resolve()
        if not resolved.is_dir():
            raise ConfigLoadError(
                f"{_SKILL_DIR_ENV} points to a missing directory: {resolved}",
                detail={"env": _SKILL_DIR_ENV, "value": env_override},
            )
        _skill_dir = resolved
        return _skill_dir

    project_root = _find_project_root()
    tech_path = project_root / "configs" / _TECH_STACK_FILENAME
    if not tech_path.is_file():
        raise ConfigLoadError(
            f"Tech stack config not found: {_TECH_STACK_FILENAME}",
            detail={"path": str(tech_path)},
        )

    try:
        with tech_path.open(encoding="utf-8") as fh:
            tech_cfg = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigLoadError(
            f"Invalid JSON in {_TECH_STACK_FILENAME}: {exc.msg}",
            detail={
                "path": str(tech_path),
                "line": exc.lineno,
                "column": exc.colno,
            },
        ) from exc

    block = tech_cfg.get(_SKILL_LOADER_BLOCK)
    if not isinstance(block, dict):
        raise ConfigLoadError(
            f"{_TECH_STACK_FILENAME} missing '{_SKILL_LOADER_BLOCK}' object",
            detail={"path": str(tech_path)},
        )

    rel_dir = block.get(_SKILL_DIR_KEY)
    if not isinstance(rel_dir, str) or not rel_dir.strip():
        raise ConfigLoadError(
            f"{_TECH_STACK_FILENAME} missing "
            f"'{_SKILL_LOADER_BLOCK}.{_SKILL_DIR_KEY}' (non-empty string)",
            detail={"path": str(tech_path)},
        )

    candidate = Path(rel_dir)
    resolved = (
        candidate if candidate.is_absolute() else project_root / candidate
    ).resolve()
    if not resolved.is_dir():
        raise ConfigLoadError(
            f"Configured skill_dir does not exist: {resolved}",
            detail={"configured": rel_dir, "resolved": str(resolved)},
        )

    _skill_dir = resolved
    return _skill_dir


def _find_project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "configs").is_dir():
            return parent
    raise ConfigLoadError(
        f"Could not locate project root above {here} (no configs/)",
        detail={"start": str(here)},
    )
