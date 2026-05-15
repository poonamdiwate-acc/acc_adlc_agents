"""DOCX output renderer — generates Word documents using python-docx."""

from __future__ import annotations

import io
import logging
from typing import Any, Dict, List

from core.output_renderers.base import OutputRenderer, RenderedOutput

logger = logging.getLogger(__name__)


class DocxRenderer(OutputRenderer):
    """Renders result as a structured Word document.
    
    Agent-aware renderer that adapts to different output structures:
    - AD-04 (Gap Detection): gap_report + gap_summary
    - DE-04 (API Contracts): openapi_spec + schema_registry
    - Generic fallback for other agents
    """

    @property
    def format_name(self) -> str:
        return "docx"

    def render(
        self,
        result: Dict[str, Any],
        *,
        agent_id: str,
        run_id: str,
    ) -> RenderedOutput:
        try:
            from docx import Document
            from docx.shared import Inches, Pt, RGBColor
            from docx.enum.table import WD_TABLE_ALIGNMENT
        except ImportError as exc:
            raise RuntimeError(
                "python-docx is required for DOCX rendering. "
                "Install with: pip install python-docx"
            ) from exc

        doc = Document()

        # Route to agent-specific renderer
        if agent_id == "AD-04" or "gap_report" in result:
            self._render_gap_detection(doc, result, agent_id, run_id)
        elif agent_id == "DE-04" or "openapi_spec" in result:
            self._render_api_contracts(doc, result, agent_id, run_id)
        else:
            self._render_generic(doc, result, agent_id, run_id)

        # Write to bytes
        buffer = io.BytesIO()
        doc.save(buffer)
        content = buffer.getvalue()

        return RenderedOutput(
            content=content,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"{agent_id}_output_{run_id}.docx",
        )

    def _render_gap_detection(
        self, doc: Any, result: Dict[str, Any], agent_id: str, run_id: str
    ) -> None:
        """Render AD-04 Gap Detection output."""
        # Title
        doc.add_heading(f"{agent_id} — Gap Detection Report", level=0)
        doc.add_paragraph(f"Run ID: {run_id}")

        # Gap Summary section
        gap_summary = result.get("gap_summary", {})
        if gap_summary:
            doc.add_heading("Gap Summary", level=1)
            summary_table = doc.add_table(rows=1, cols=2)
            summary_table.style = "Table Grid"
            hdr_cells = summary_table.rows[0].cells
            hdr_cells[0].text = "Metric"
            hdr_cells[1].text = "Value"

            summary_fields = [
                ("Total Requirements Analysed", gap_summary.get("total_requirements_analysed", 0)),
                ("Total Gaps Found", gap_summary.get("total_gaps_found", 0)),
                ("Blocking Gaps", gap_summary.get("blocking_gaps", 0)),
                ("Overall Quality", gap_summary.get("overall_quality", "N/A")),
                ("Recommendation", gap_summary.get("recommendation", "N/A")),
            ]
            for label, value in summary_fields:
                row = summary_table.add_row().cells
                row[0].text = label
                row[1].text = str(value)

            # Gaps by severity
            by_severity = gap_summary.get("gaps_by_severity", {})
            if by_severity:
                doc.add_heading("Gaps by Severity", level=2)
                sev_table = doc.add_table(rows=1, cols=2)
                sev_table.style = "Table Grid"
                sev_table.rows[0].cells[0].text = "Severity"
                sev_table.rows[0].cells[1].text = "Count"
                for sev, count in by_severity.items():
                    row = sev_table.add_row().cells
                    row[0].text = sev
                    row[1].text = str(count)

        # Gap Report section
        gap_report: List[Dict[str, Any]] = result.get("gap_report", [])
        if gap_report:
            doc.add_heading("Gap Report", level=1)
            table = doc.add_table(rows=1, cols=6)
            table.style = "Table Grid"
            headers = ["Gap ID", "Req Ref", "Type", "Severity", "Description", "Recommendation"]
            for i, header in enumerate(headers):
                table.rows[0].cells[i].text = header

            for gap in gap_report:
                row = table.add_row().cells
                row[0].text = gap.get("gap_id", "")
                row[1].text = gap.get("req_id_ref") or "N/A"
                row[2].text = gap.get("gap_type", "")
                row[3].text = gap.get("severity", "")
                row[4].text = gap.get("description", "")
                row[5].text = gap.get("recommendation", "")
        else:
            doc.add_heading("Gap Report", level=1)
            doc.add_paragraph("No gaps detected — requirements are clean.")

    def _render_api_contracts(
        self, doc: Any, result: Dict[str, Any], agent_id: str, run_id: str
    ) -> None:
        """Render DE-04 API Contracts output."""
        # Title
        doc.add_heading(f"{agent_id} — API Contracts Report", level=0)
        doc.add_paragraph(f"Run ID: {run_id}")

        # Schema Registry section
        schema_registry = result.get("schema_registry", {})
        if schema_registry:
            doc.add_heading("Schema Registry Summary", level=1)
            summary_table = doc.add_table(rows=1, cols=2)
            summary_table.style = "Table Grid"
            hdr_cells = summary_table.rows[0].cells
            hdr_cells[0].text = "Metric"
            hdr_cells[1].text = "Value"

            summary_fields = [
                ("Total Requirements Analysed", schema_registry.get("total_requirements_analysed", 0)),
                ("Total Contracts Generated", schema_registry.get("total_contracts_generated", 0)),
                ("Uncovered Requirements", schema_registry.get("uncovered_requirements", 0)),
                ("Registry Summary", schema_registry.get("registry_summary", "N/A")),
                ("Recommendation", schema_registry.get("recommendation", "N/A")),
            ]
            for label, value in summary_fields:
                row = summary_table.add_row().cells
                row[0].text = label
                row[1].text = str(value)

            # Contracts by category
            by_category = schema_registry.get("contracts_by_category", {})
            if by_category:
                doc.add_heading("Contracts by Category", level=2)
                cat_table = doc.add_table(rows=1, cols=2)
                cat_table.style = "Table Grid"
                cat_table.rows[0].cells[0].text = "Category"
                cat_table.rows[0].cells[1].text = "Count"
                for cat, count in by_category.items():
                    if count > 0:  # Only show categories with contracts
                        row = cat_table.add_row().cells
                        row[0].text = cat
                        row[1].text = str(count)

        # OpenAPI Spec section
        openapi_spec: List[Dict[str, Any]] = result.get("openapi_spec", [])
        logger.info(
            "DOCX Renderer: Processing %d API contracts for agent=%s run=%s",
            len(openapi_spec), agent_id, run_id
        )
        
        if openapi_spec:
            doc.add_heading("API Contracts", level=1)
            
            for idx, spec in enumerate(openapi_spec, start=1):
                # Each contract as a section
                endpoint_name = spec.get('endpoint_name', 'N/A')
                logger.debug(
                    "DOCX Renderer: Rendering contract %d/%d: %s",
                    idx, len(openapi_spec), endpoint_name
                )
                doc.add_heading(f"{endpoint_name}", level=2)
                
                # Contract details table
                details_table = doc.add_table(rows=7, cols=2)
                details_table.style = "Table Grid"
                
                details = [
                    ("Spec ID", spec.get("spec_id", "")),
                    ("HTTP Method", spec.get("http_method", "")),
                    ("Path", spec.get("path", "")),
                    ("Category", spec.get("contract_category", "")),
                    ("Description", spec.get("description", "")),
                    ("Requirement Refs", ", ".join(spec.get("req_id_refs", [])) or "None"),
                    ("Entity Refs", ", ".join(spec.get("entity_refs", [])) or "None"),
                ]
                
                for idx, (label, value) in enumerate(details):
                    details_table.rows[idx].cells[0].text = label
                    details_table.rows[idx].cells[1].text = str(value)
                
                # Add spacing between contracts
                doc.add_paragraph()
        else:
            doc.add_heading("API Contracts", level=1)
            doc.add_paragraph("No API contracts generated.")

    def _render_generic(
        self, doc: Any, result: Dict[str, Any], agent_id: str, run_id: str
    ) -> None:
        """Generic fallback renderer for unknown agent types."""
        import json
        
        doc.add_heading(f"{agent_id} — Agent Output", level=0)
        doc.add_paragraph(f"Run ID: {run_id}")
        
        doc.add_heading("Raw Output", level=1)
        doc.add_paragraph(json.dumps(result, indent=2, ensure_ascii=False))
