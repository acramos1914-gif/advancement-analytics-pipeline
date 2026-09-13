"""Executive PDF report stage: one-page KPI + data quality summary.

Produces a single-page PDF meant to be skimmed by a VP of Advancement or
a board member in under a minute -- KPI highlights up top, a data quality
scorecard below so the numbers' trustworthiness is never hidden.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.metrics_engine import MetricResult
from src.validate import DataQualityReport

UNIT_DISPLAY = {
    "percent": lambda v: f"{v:.2f}%",
    "currency": lambda v: f"${v:,.2f}",
    "count": lambda v: f"{int(v):,}",
}

STATUS_COLORS = {
    "pass": colors.HexColor("#C6EFCE"),
    "fail_critical": colors.HexColor("#FFC7CE"),
    "fail_warning": colors.HexColor("#FFEB9C"),
}


def _format_value(metric: MetricResult) -> str:
    """Format a metric's raw value according to its unit for display."""
    formatter = UNIT_DISPLAY.get(metric.unit, str)
    return formatter(metric.value)


def _kpi_table(metrics: list[MetricResult]) -> Table:
    """Build the KPI highlights table."""
    data = [["KPI", "Value"]] + [[m.name, _format_value(m)] for m in metrics]
    table = Table(data, colWidths=[3.6 * inch, 1.8 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _dq_table(dq_report: DataQualityReport) -> Table:
    """Build the data quality scorecard table."""
    data = [["Rule", "Severity", "Status", "Affected"]]
    row_colors = []
    for rule in dq_report.rule_results:
        data.append([rule.rule_id, rule.severity.upper(), rule.status.upper(), f"{rule.records_affected:,}"])
        if rule.status == "pass":
            row_colors.append(STATUS_COLORS["pass"])
        elif rule.severity == "critical":
            row_colors.append(STATUS_COLORS["fail_critical"])
        else:
            row_colors.append(STATUS_COLORS["fail_warning"])

    table = Table(data, colWidths=[2.6 * inch, 1.1 * inch, 1.0 * inch, 1.0 * inch])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i, color in enumerate(row_colors, start=1):
        style.append(("BACKGROUND", (0, i), (-1, i), color))
    table.setStyle(TableStyle(style))
    return table


def generate_pdf_report(
    metrics: list[MetricResult], dq_report: DataQualityReport, output_path: str | Path
) -> Path:
    """Build the one-page executive PDF summary.

    Args:
        metrics: Computed KPI results.
        dq_report: Data quality validation results.
        output_path: Destination path for the .pdf file.

    Returns:
        The path the PDF was written to.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Title"], fontSize=18, spaceAfter=2)
    subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=10, textColor=colors.grey)
    section_style = ParagraphStyle("Section", parent=styles["Heading2"], fontSize=13, spaceBefore=14, spaceAfter=6)
    status_style = ParagraphStyle("Status", parent=styles["Normal"], fontSize=11, spaceAfter=8)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
    )

    elements = [
        Paragraph("Carmari Academy", title_style),
        Paragraph(
            f"Advancement Executive Summary &mdash; generated {date.today().isoformat()}", subtitle_style
        ),
        Paragraph("KPI Highlights", section_style),
        _kpi_table(metrics),
        Paragraph("Data Quality Scorecard", section_style),
        Paragraph(
            f"Overall status: <b>{dq_report.overall_status.upper()}</b> &nbsp;&nbsp; "
            f"{dq_report.critical_failures} critical issue(s), {dq_report.warning_failures} warning(s) "
            f"across {dq_report.total_records:,} records.",
            status_style,
        ),
        _dq_table(dq_report),
        Spacer(1, 12),
        Paragraph(
            "All data synthetic. Carmari Academy is a fictional institution used for portfolio "
            "demonstration purposes only.",
            ParagraphStyle("Footer", parent=styles["Normal"], fontSize=7, textColor=colors.grey),
        ),
    ]

    doc.build(elements)
    return path
