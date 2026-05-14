"""DOCX output renderer — generates Word documents using python-docx."""

from __future__ import annotations

import io
import logging
from typing import Any, Dict, List

from core.output_renderers.base import OutputRenderer, RenderedOutput

logger = logging.getLogger(__name__)


class DocxRenderer(OutputRenderer):
    """Renders result as a structured Word document."""

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

        # Write to bytes
        buffer = io.BytesIO()
        doc.save(buffer)
        content = buffer.getvalue()

        return RenderedOutput(
            content=content,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"{agent_id}_output_{run_id}.docx",
        )
