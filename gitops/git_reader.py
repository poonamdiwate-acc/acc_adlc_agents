"""Audit-repo reader — two backends behind one interface.

Agents call ``GitReader.read_json(path)`` and don't care whether the bytes
came from a local fixture directory or a real GitPython clone of the audit
repo. The choice is per-agent and lives in
``<agent>_Config.json#git_reader.enabled``:

* ``enabled: false`` → :class:`LocalFileGitReader` reads
  ``{tech.git_reader.local_audit_dir}/{path}`` from disk. No git
  dependency, no network.
* ``enabled: true``  → :class:`GitPythonReader` clones / fetches the audit
  repo into ``{tech.git_reader.cache_dir}`` and reads ``{path}`` at the
  configured branch tip.

Use :func:`create_git_reader` to construct the right one from an agent
config + tech-stack config — never instantiate the concrete classes
directly in agent code.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from core.exceptions import GitReadError

logger = logging.getLogger(__name__)


_ENV_PREFIX = "ENV:"


@runtime_checkable
class GitReader(Protocol):
    """Read-only interface — both backends implement this."""

    async def read_json(self, path: str) -> Dict[str, Any]:
        """Return the parsed JSON object at ``path``.

        ``path`` is the audit-repo-relative path, already templated for
        ``{run_id}`` etc. Raises :class:`GitReadError` if the file is
        missing or invalid.
        """
        ...


# ---------------------------------------------------------------- local file

class LocalFileGitReader:
    """Read audit-repo files from a local directory — no git involved.

    Default for ``ENV=dev`` and any agent whose
    ``git_reader.enabled`` is ``false``.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir).resolve()
        if not self._base_dir.is_dir():
            raise GitReadError(
                f"local_audit_dir does not exist: {self._base_dir}",
                detail={"base_dir": str(self._base_dir)},
            )
        logger.info("LocalFileGitReader base_dir=%s", self._base_dir)

    async def read_json(self, path: str) -> Dict[str, Any]:
        full_path = (self._base_dir / path).resolve()
        # Containment check — refuse to read outside base_dir even if the
        # caller passes ".." in the path template.
        if self._base_dir not in full_path.parents and full_path != self._base_dir:
            raise GitReadError(
                "Resolved path escapes local_audit_dir",
                detail={"path": path, "resolved": str(full_path)},
            )
        if not full_path.is_file():
            raise GitReadError(
                f"Local audit file not found: {path}",
                detail={
                    "path": path,
                    "resolved": str(full_path),
                    "base_dir": str(self._base_dir),
                },
            )
        try:
            text = full_path.read_text(encoding="utf-8")
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GitReadError(
                f"Invalid JSON in local audit file: {path}",
                detail={
                    "path": str(full_path),
                    "line": exc.lineno,
                    "column": exc.colno,
                },
            ) from exc


# ---------------------------------------------------------------- real git

class GitPythonReader:
    """Read audit-repo files from a real git remote via GitPython.

    Maintains a single shallow clone in ``cache_dir`` and re-fetches the
    branch tip on each read. ``git show`` is used to stream a single file
    out of the working tree without checking it out.

    Network I/O happens in a thread executor so the FastAPI event loop is
    not blocked.
    """

    def __init__(
        self,
        *,
        repo_url: str,
        branch: str,
        cache_dir: Path,
        token: Optional[str] = None,
    ) -> None:
        try:
            import git  # type: ignore  # noqa: F401
        except ImportError as exc:  # pragma: no cover — only fires when GitPython missing
            raise GitReadError(
                "GitPython is not installed but git_reader.enabled is true. "
                "Install with: pip install GitPython",
                detail={"missing_package": "GitPython"},
            ) from exc

        self._repo_url = _inject_token(repo_url, token)
        self._safe_url = _redact_token(self._repo_url)
        self._branch = branch
        self._cache_dir = Path(cache_dir).resolve()
        self._lock = asyncio.Lock()
        self._repo = None  # set on first use

    async def read_json(self, path: str) -> Dict[str, Any]:
        async with self._lock:
            text = await asyncio.get_running_loop().run_in_executor(
                None, self._sync_read, path
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GitReadError(
                f"Invalid JSON at {self._branch}:{path}",
                detail={
                    "path": path,
                    "branch": self._branch,
                    "line": exc.lineno,
                    "column": exc.colno,
                },
            ) from exc

    def _sync_read(self, path: str) -> str:
        import git  # type: ignore

        self._ensure_repo()
        try:
            return self._repo.git.show(f"{self._branch}:{path}")
        except git.GitCommandError as exc:
            raise GitReadError(
                f"git show failed for {self._branch}:{path}",
                detail={
                    "path": path,
                    "branch": self._branch,
                    "stderr": str(exc.stderr or "").strip()[:500],
                    "status": exc.status,
                },
            ) from exc

    def _ensure_repo(self) -> None:
        import git  # type: ignore

        if self._repo is not None:
            # Refresh ref tip from origin before each read.
            try:
                self._repo.remotes.origin.fetch(self._branch, depth=1)
            except git.GitCommandError as exc:
                raise GitReadError(
                    f"git fetch failed from {self._safe_url}",
                    detail={"branch": self._branch, "stderr": str(exc.stderr or "")[:500]},
                ) from exc
            return

        if (self._cache_dir / ".git").is_dir():
            logger.info("Reusing audit-repo cache: %s", self._cache_dir)
            self._repo = git.Repo(self._cache_dir)
            try:
                self._repo.remotes.origin.set_url(self._repo_url)
                self._repo.remotes.origin.fetch(self._branch, depth=1)
            except git.GitCommandError as exc:
                raise GitReadError(
                    f"git fetch failed from {self._safe_url}",
                    detail={"branch": self._branch, "stderr": str(exc.stderr or "")[:500]},
                ) from exc
            return

        logger.info("Cloning audit-repo into %s (depth=1, branch=%s)",
                    self._cache_dir, self._branch)
        self._cache_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._repo = git.Repo.clone_from(
                self._repo_url,
                self._cache_dir,
                depth=1,
                branch=self._branch,
            )
        except git.GitCommandError as exc:
            raise GitReadError(
                f"git clone failed from {self._safe_url}",
                detail={"branch": self._branch, "stderr": str(exc.stderr or "")[:500]},
            ) from exc


# ---------------------------------------------------------------- factory

def create_git_reader(
    *,
    agent_git_reader_cfg: Dict[str, Any],
    tech_git_reader_cfg: Dict[str, Any],
    project_root: Path,
) -> GitReader:
    """Pick the right backend for one agent based on its config.

    ``agent_git_reader_cfg`` is ``<agent>_Config.json#git_reader``.
    ``tech_git_reader_cfg`` is ``ADLC_Tech_Stack_Config.json#git_reader``.
    """
    enabled = bool(agent_git_reader_cfg.get("enabled", False))

    if not enabled:
        rel = tech_git_reader_cfg.get("local_audit_dir", "tests/integration/fixtures")
        base_dir = (project_root / rel).resolve()
        return LocalFileGitReader(base_dir=base_dir)

    repo_url = _resolve_env_ref(agent_git_reader_cfg.get("repo_url"))
    if not repo_url:
        raise GitReadError(
            "git_reader.enabled is true but repo_url could not be resolved",
            detail={"hint": "set $ADLC_AUDIT_REPO_URL"},
        )
    token = _resolve_env_ref(agent_git_reader_cfg.get("auth_method"))
    branch = agent_git_reader_cfg.get("branch", "main")
    cache_rel = tech_git_reader_cfg.get("cache_dir", ".git_cache")
    cache_dir = (project_root / cache_rel).resolve()
    return GitPythonReader(
        repo_url=repo_url,
        branch=branch,
        cache_dir=cache_dir,
        token=token,
    )


# ---------------------------------------------------------------- helpers

def _resolve_env_ref(value: Optional[str]) -> Optional[str]:
    """Resolve a ``"ENV:FOO"`` string to ``os.environ["FOO"]``.

    Non-``ENV:`` strings are returned unchanged. ``None``/empty returns
    ``None``. Missing env vars return ``None`` so the caller can decide
    whether the absence is fatal.
    """
    if not value:
        return None
    if not value.startswith(_ENV_PREFIX):
        return value
    var_name = value[len(_ENV_PREFIX):].strip()
    if not var_name:
        return None
    return os.environ.get(var_name)


def _inject_token(repo_url: str, token: Optional[str]) -> str:
    """Embed ``token`` into an https://... URL for non-interactive auth.

    No-op when token is missing or URL is not https. Handles GitHub /
    GitLab style by using ``x-access-token`` as username.
    """
    if not token or not repo_url.startswith("https://"):
        return repo_url
    # Don't double-inject if the URL already has credentials.
    if "@" in repo_url.split("//", 1)[1]:
        return repo_url
    return repo_url.replace("https://", f"https://x-access-token:{token}@", 1)


_TOKEN_PATTERN = re.compile(r"(https://)[^@/]+@")


def _redact_token(repo_url: str) -> str:
    """Strip credentials before logging."""
    return _TOKEN_PATTERN.sub(r"\1***@", repo_url)
