"""Unit tests for multi-format input parsers and output renderers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from core.input_parsers.base import ParsedDocument
from core.input_parsers.json_parser import JsonParser
from core.input_parsers.html_parser import HtmlParser
from core.output_renderers.json_renderer import JsonRenderer
from core.output_renderers.html_renderer import HtmlRenderer
from core.output_renderers.docx_renderer import DocxRenderer
from core.format_handler import (
    get_parser_for_content_type,
    get_parser_for_extension,
    get_renderer_for_format,
    parse_input,
    render_output,
    SUPPORTED_INPUT_FORMATS,
    SUPPORTED_OUTPUT_FORMATS,
)


# ============================================================ JSON Parser


class TestJsonParser:
    def test_valid_json(self):
        parser = JsonParser()
        content = json.dumps({"business_case": "test"}).encode("utf-8")
        result = parser.parse(content)
        assert result.structured_data == {"business_case": "test"}
        assert result.source_format == "json"
        assert not result.needs_llm_extraction

    def test_empty_json_raises(self):
        parser = JsonParser()
        with pytest.raises(ValueError, match="Empty JSON"):
            parser.parse(b"")

    def test_invalid_json_raises(self):
        parser = JsonParser()
        with pytest.raises(ValueError, match="Invalid JSON"):
            parser.parse(b"not json at all")

    def test_non_object_json_raises(self):
        parser = JsonParser()
        with pytest.raises(ValueError, match="must be an object"):
            parser.parse(b'[1, 2, 3]')

    def test_supported_content_types(self):
        parser = JsonParser()
        assert "application/json" in parser.supported_content_types


# ============================================================ HTML Parser


class TestHtmlParser:
    def test_valid_html(self):
        parser = HtmlParser()
        html = b"""
        <html>
        <body>
            <h1>Business Case</h1>
            <p>Customer portal authentication</p>
            <h2>Requirements</h2>
            <table>
                <tr><th>ID</th><th>Title</th></tr>
                <tr><td>REQ-001</td><td>User login</td></tr>
            </table>
        </body>
        </html>
        """
        result = parser.parse(html)
        assert result.source_format == "html"
        assert result.needs_llm_extraction is True
        assert "Business Case" in result.sections
        assert len(result.tables) == 1
        assert result.tables[0][0] == ["ID", "Title"]

    def test_empty_html_raises(self):
        parser = HtmlParser()
        with pytest.raises(ValueError, match="Empty HTML"):
            parser.parse(b"")

    def test_no_text_html_raises(self):
        parser = HtmlParser()
        with pytest.raises(ValueError, match="no text content"):
            parser.parse(b"<html><body><script>var x=1;</script></body></html>")


# ============================================================ Parser Registry


class TestParserRegistry:
    def test_json_content_type(self):
        parser = get_parser_for_content_type("application/json")
        assert parser is not None
        assert isinstance(parser, JsonParser)

    def test_html_content_type(self):
        parser = get_parser_for_content_type("text/html")
        assert parser is not None
        assert isinstance(parser, HtmlParser)

    def test_docx_content_type(self):
        parser = get_parser_for_content_type(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert parser is not None

    def test_pdf_content_type(self):
        parser = get_parser_for_content_type("application/pdf")
        assert parser is not None

    def test_unknown_content_type(self):
        parser = get_parser_for_content_type("application/octet-stream")
        assert parser is None

    def test_extension_json(self):
        parser = get_parser_for_extension("report.json")
        assert parser is not None

    def test_extension_docx(self):
        parser = get_parser_for_extension("requirements.docx")
        assert parser is not None

    def test_extension_pdf(self):
        parser = get_parser_for_extension("spec.pdf")
        assert parser is not None

    def test_extension_html(self):
        parser = get_parser_for_extension("page.html")
        assert parser is not None


# ============================================================ JSON Renderer


class TestJsonRenderer:
    def test_render(self):
        renderer = JsonRenderer()
        result = {"gap_report": [], "gap_summary": {"total_gaps_found": 0}}
        output = renderer.render(result, agent_id="PL-01", run_id="test-001")
        assert output.content_type == "application/json"
        assert output.filename == "PL-01_output_test-001.json"
        parsed = json.loads(output.content)
        assert parsed == result


# ============================================================ HTML Renderer


class TestHtmlRenderer:
    def test_render_with_gaps(self):
        renderer = HtmlRenderer()
        result = {
            "gap_report": [
                {
                    "gap_id": "GAP-001",
                    "req_id_ref": "REQ-001",
                    "gap_type": "ambiguous_language",
                    "severity": "high",
                    "description": "Vague term",
                    "recommendation": "Be specific",
                    "auto_resolvable": False,
                }
            ],
            "gap_summary": {
                "total_requirements_analysed": 3,
                "total_gaps_found": 1,
                "blocking_gaps": 1,
                "overall_quality": "needs_attention",
                "recommendation": "resolve_blocking_gaps_first",
                "gaps_by_severity": {"critical": 0, "high": 1, "medium": 0, "low": 0},
            },
        }
        output = renderer.render(result, agent_id="PL-01", run_id="test-001")
        assert "text/html" in output.content_type
        html = output.content.decode("utf-8")
        assert "GAP-001" in html
        assert "ambiguous_language" in html

    def test_render_empty_report(self):
        renderer = HtmlRenderer()
        result = {"gap_report": [], "gap_summary": {"total_gaps_found": 0}}
        output = renderer.render(result, agent_id="PL-01", run_id="test-002")
        html = output.content.decode("utf-8")
        assert "No gaps detected" in html


# ============================================================ DOCX Renderer


class TestDocxRenderer:
    def test_render_produces_docx(self):
        renderer = DocxRenderer()
        result = {
            "gap_report": [
                {
                    "gap_id": "GAP-001",
                    "req_id_ref": "REQ-001",
                    "gap_type": "ambiguous_language",
                    "severity": "high",
                    "description": "Vague term used",
                    "recommendation": "Define explicitly",
                    "auto_resolvable": False,
                }
            ],
            "gap_summary": {
                "total_requirements_analysed": 5,
                "total_gaps_found": 1,
                "blocking_gaps": 1,
                "overall_quality": "needs_attention",
                "recommendation": "resolve_blocking_gaps_first",
                "gaps_by_severity": {"critical": 0, "high": 1, "medium": 0, "low": 0},
            },
        }
        output = renderer.render(result, agent_id="PL-01", run_id="test-001")
        assert "wordprocessingml" in output.content_type
        # DOCX files start with PK (ZIP header)
        assert output.content[:2] == b"PK"


# ============================================================ Format Handler


class TestFormatHandler:
    def test_supported_formats(self):
        assert "json" in SUPPORTED_INPUT_FORMATS
        assert "docx" in SUPPORTED_INPUT_FORMATS
        assert "pdf" in SUPPORTED_INPUT_FORMATS
        assert "html" in SUPPORTED_INPUT_FORMATS
        assert "json" in SUPPORTED_OUTPUT_FORMATS
        assert "docx" in SUPPORTED_OUTPUT_FORMATS
        assert "pdf" in SUPPORTED_OUTPUT_FORMATS
        assert "html" in SUPPORTED_OUTPUT_FORMATS

    @pytest.mark.asyncio
    async def test_parse_input_json(self):
        content = json.dumps({"business_case": "test"}).encode("utf-8")
        result = await parse_input(
            content, "application/json", agent_id="PL-01"
        )
        assert result == {"business_case": "test"}

    @pytest.mark.asyncio
    async def test_parse_input_unsupported_format(self):
        with pytest.raises(ValueError, match="Unsupported input format"):
            await parse_input(
                b"data", "application/octet-stream", agent_id="PL-01"
            )

    def test_render_output_json(self):
        result = {"gap_report": []}
        output = render_output(result, "json", agent_id="PL-01", run_id="r1")
        assert output.content_type == "application/json"

    def test_render_output_unsupported(self):
        with pytest.raises(ValueError, match="Unsupported output format"):
            render_output({}, "xml", agent_id="PL-01", run_id="r1")

    def test_renderer_registry(self):
        assert get_renderer_for_format("json") is not None
        assert get_renderer_for_format("docx") is not None
        assert get_renderer_for_format("pdf") is not None
        assert get_renderer_for_format("html") is not None
        assert get_renderer_for_format("xml") is None
