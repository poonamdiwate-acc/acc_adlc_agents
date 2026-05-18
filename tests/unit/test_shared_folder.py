"""Unit tests for core/shared_folder.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.shared_folder import (
    _get_content_type,
    list_input_files,
    read_inputs,
    resolve_input_folder,
    resolve_output_folder,
    write_output,
)


# ============================================================ resolve_input_folder


class TestResolveInputFolder:
    def test_valid_folder(self, tmp_path):
        thread_dir = tmp_path / "thread1" / "bs_docs"
        thread_dir.mkdir(parents=True)
        result = resolve_input_folder(str(tmp_path), "thread1", "bs_docs")
        assert result == thread_dir

    def test_missing_folder_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Input folder not found"):
            resolve_input_folder(str(tmp_path), "thread1", "bs_docs")


# ============================================================ resolve_output_folder


class TestResolveOutputFolder:
    def test_creates_folder(self, tmp_path):
        result = resolve_output_folder(str(tmp_path), "thread1", "gap_response")
        assert result.is_dir()
        assert result == tmp_path / "thread1" / "gap_response"

    def test_existing_folder_ok(self, tmp_path):
        folder = tmp_path / "thread1" / "gap_response"
        folder.mkdir(parents=True)
        result = resolve_output_folder(str(tmp_path), "thread1", "gap_response")
        assert result == folder


# ============================================================ list_input_files


class TestListInputFiles:
    def test_lists_supported_files(self, tmp_path):
        (tmp_path / "spec.json").write_text("{}")
        (tmp_path / "req.docx").write_bytes(b"PK")
        (tmp_path / "readme.txt").write_text("ignore me")
        files = list_input_files(tmp_path)
        names = [f.name for f in files]
        assert "spec.json" in names
        assert "req.docx" in names
        assert "readme.txt" not in names

    def test_empty_folder_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No supported input files"):
            list_input_files(tmp_path)

    def test_only_unsupported_raises(self, tmp_path):
        (tmp_path / "data.csv").write_text("a,b,c")
        with pytest.raises(ValueError, match="No supported input files"):
            list_input_files(tmp_path)


# ============================================================ content type mapping


class TestGetContentType:
    def test_json(self):
        assert _get_content_type(Path("file.json")) == "application/json"

    def test_docx(self):
        ct = _get_content_type(Path("file.docx"))
        assert "wordprocessingml" in ct

    def test_pdf(self):
        assert _get_content_type(Path("file.pdf")) == "application/pdf"

    def test_html(self):
        assert _get_content_type(Path("file.html")) == "text/html"

    def test_unknown(self):
        assert _get_content_type(Path("file.xyz")) == "application/octet-stream"


# ============================================================ read_inputs


class TestReadInputs:
    @pytest.mark.asyncio
    async def test_reads_json_files(self, tmp_path):
        input_dir = tmp_path / "t1" / "bs_docs"
        input_dir.mkdir(parents=True)
        (input_dir / "data.json").write_text(
            json.dumps({"business_case": "test case", "project_context": {"squad": "auth"}}),
            encoding="utf-8",
        )
        result = await read_inputs(
            base_path=str(tmp_path),
            thread_id="t1",
            input_subfolder="bs_docs",
            agent_id="PL-01",
        )
        assert result["business_case"] == "test case"
        assert result["project_context"]["squad"] == "auth"

    @pytest.mark.asyncio
    async def test_merges_multiple_json_files(self, tmp_path):
        input_dir = tmp_path / "t1" / "bs_docs"
        input_dir.mkdir(parents=True)
        (input_dir / "01_business.json").write_text(
            json.dumps({"business_case": "login system"}),
            encoding="utf-8",
        )
        (input_dir / "02_context.json").write_text(
            json.dumps({"project_context": {"squad": "auth"}}),
            encoding="utf-8",
        )
        result = await read_inputs(
            base_path=str(tmp_path),
            thread_id="t1",
            input_subfolder="bs_docs",
            agent_id="PL-01",
        )
        assert result["business_case"] == "login system"
        assert result["project_context"]["squad"] == "auth"

    @pytest.mark.asyncio
    async def test_missing_folder_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Input folder not found"):
            await read_inputs(
                base_path=str(tmp_path),
                thread_id="missing",
                input_subfolder="bs_docs",
                agent_id="PL-01",
            )


# ============================================================ write_output


class TestWriteOutput:
    def test_writes_json(self, tmp_path):
        result = {"gap_report": [], "gap_summary": {"total_gaps_found": 0}}
        path = write_output(
            base_path=str(tmp_path),
            thread_id="t1",
            output_subfolder="gap_response",
            agent_id="PL-01",
            result=result,
            output_format="json",
        )
        assert path.is_file()
        assert path.name == "PL-01_output.json"
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content == result

    def test_creates_output_folder(self, tmp_path):
        result = {"gap_report": []}
        path = write_output(
            base_path=str(tmp_path),
            thread_id="t2",
            output_subfolder="gap_response",
            agent_id="PL-01",
            result=result,
        )
        assert (tmp_path / "t2" / "gap_response").is_dir()
        assert path.is_file()
