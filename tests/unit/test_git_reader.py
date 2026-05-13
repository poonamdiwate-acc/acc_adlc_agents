"""Unit tests for :mod:`git.git_reader`.

The LocalFileGitReader and the factory are tested directly. The
GitPythonReader is import-checked but not exercised against a real remote
— that's an integration concern.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.exceptions import GitReadError
from gitops.git_reader import (
    LocalFileGitReader,
    create_git_reader,
    _inject_token,
    _redact_token,
    _resolve_env_ref,
)


class TestLocalFileGitReader:
    @pytest.mark.asyncio
    async def test_reads_json_file(self, tmp_path: Path):
        payload = {"structured_requirements": [{"req_id": "REQ-001"}]}
        target = tmp_path / "runs" / "abc" / "plan" / "PL-01_output.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(payload), encoding="utf-8")

        reader = LocalFileGitReader(base_dir=tmp_path)
        result = await reader.read_json("runs/abc/plan/PL-01_output.json")
        assert result == payload

    @pytest.mark.asyncio
    async def test_missing_file_raises(self, tmp_path: Path):
        reader = LocalFileGitReader(base_dir=tmp_path)
        with pytest.raises(GitReadError) as exc_info:
            await reader.read_json("runs/missing/plan/PL-01_output.json")
        assert "not found" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self, tmp_path: Path):
        target = tmp_path / "bad.json"
        target.write_text("{this is not json", encoding="utf-8")

        reader = LocalFileGitReader(base_dir=tmp_path)
        with pytest.raises(GitReadError) as exc_info:
            await reader.read_json("bad.json")
        assert "invalid json" in exc_info.value.message.lower()

    def test_missing_base_dir_raises(self, tmp_path: Path):
        with pytest.raises(GitReadError):
            LocalFileGitReader(base_dir=tmp_path / "does-not-exist")

    @pytest.mark.asyncio
    async def test_path_traversal_refused(self, tmp_path: Path):
        sibling = tmp_path.parent / "outside.json"
        sibling.write_text("{}", encoding="utf-8")
        try:
            reader = LocalFileGitReader(base_dir=tmp_path)
            with pytest.raises(GitReadError) as exc_info:
                await reader.read_json("../outside.json")
            assert "escapes" in exc_info.value.message.lower()
        finally:
            if sibling.exists():
                sibling.unlink()


class TestFactory:
    def test_picks_local_when_disabled(self, tmp_path: Path):
        local_dir = tmp_path / "fixtures"
        local_dir.mkdir()
        reader = create_git_reader(
            agent_git_reader_cfg={"enabled": False},
            tech_git_reader_cfg={"local_audit_dir": "fixtures"},
            project_root=tmp_path,
        )
        assert isinstance(reader, LocalFileGitReader)

    def test_default_is_local(self, tmp_path: Path):
        (tmp_path / "tests" / "integration" / "fixtures").mkdir(parents=True)
        reader = create_git_reader(
            agent_git_reader_cfg={},  # no enabled key at all
            tech_git_reader_cfg={},   # no local_audit_dir — uses default
            project_root=tmp_path,
        )
        assert isinstance(reader, LocalFileGitReader)

    def test_enabled_without_repo_url_raises(self, tmp_path: Path):
        with pytest.raises(GitReadError) as exc_info:
            create_git_reader(
                agent_git_reader_cfg={"enabled": True, "repo_url": "ENV:NOT_SET_XYZ"},
                tech_git_reader_cfg={"cache_dir": ".git_cache"},
                project_root=tmp_path,
            )
        assert "repo_url" in exc_info.value.message.lower()


class TestEnvHelpers:
    def test_resolves_env_prefix(self, monkeypatch):
        monkeypatch.setenv("FOO_BAR_BAZ", "hello")
        assert _resolve_env_ref("ENV:FOO_BAR_BAZ") == "hello"

    def test_missing_env_returns_none(self, monkeypatch):
        monkeypatch.delenv("DEFINITELY_NOT_SET", raising=False)
        assert _resolve_env_ref("ENV:DEFINITELY_NOT_SET") is None

    def test_literal_value_passes_through(self):
        assert _resolve_env_ref("https://example.com/repo.git") == "https://example.com/repo.git"

    def test_none_and_empty(self):
        assert _resolve_env_ref(None) is None
        assert _resolve_env_ref("") is None

    def test_token_injection(self):
        url = _inject_token("https://github.com/org/repo.git", "ghp_abc")
        assert url == "https://x-access-token:ghp_abc@github.com/org/repo.git"

    def test_token_injection_no_op_without_token(self):
        url = _inject_token("https://github.com/org/repo.git", None)
        assert url == "https://github.com/org/repo.git"

    def test_token_injection_no_op_for_ssh(self):
        url = _inject_token("git@github.com:org/repo.git", "ghp_abc")
        assert url == "git@github.com:org/repo.git"

    def test_token_redaction(self):
        url = "https://x-access-token:ghp_abcdef@github.com/org/repo.git"
        assert _redact_token(url) == "https://***@github.com/org/repo.git"
