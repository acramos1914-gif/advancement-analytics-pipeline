"""Pipeline orchestrator: ingest -> validate -> compute metrics -> write outputs.

This is the single entry point that turns a raw CRM export into a governed
executive deliverable with zero manual steps. Run it as:

    python -m src.pipeline run --input data/sample_donors.csv --output-dir output/
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

import click
import pandas as pd

from src.excel_report import generate_excel_report
from src.hyper_writer import write_hyper
from src.ingest import IngestError, ingest
from src.metrics_engine import MetricResult, MetricsConfigError, compute
from src.pdf_report import generate_pdf_report
from src.validate import DataQualityReport, ValidationConfigError, validate

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_METRICS_REGISTRY = PROJECT_ROOT / "config" / "metrics_registry.yaml"
DEFAULT_VALIDATION_RULES = PROJECT_ROOT / "config" / "validation_rules.yaml"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("pipeline")


class PipelineError(Exception):
    """Raised when a pipeline stage fails in a way the user needs to act on."""


@dataclass
class PipelineResult:
    """Paths and summary data produced by a successful pipeline run."""

    hyper_path: Path
    excel_path: Path
    pdf_path: Path
    metrics: list[MetricResult]
    dq_report: DataQualityReport
    record_count: int


@contextmanager
def _timed_stage(name: str) -> Iterator[None]:
    """Log the start, duration, and success/failure of a pipeline stage."""
    logger.info("-> %s: starting", name)
    start = time.perf_counter()
    try:
        yield
    except Exception:
        elapsed = time.perf_counter() - start
        logger.error("-> %s: FAILED after %.2fs", name, elapsed)
        raise
    else:
        elapsed = time.perf_counter() - start
        logger.info("-> %s: done in %.2fs", name, elapsed)


def run_pipeline(
    input_path: str | Path,
    output_dir: str | Path,
    metrics_registry_path: str | Path = DEFAULT_METRICS_REGISTRY,
    validation_rules_path: str | Path = DEFAULT_VALIDATION_RULES,
    as_of: date | None = None,
) -> PipelineResult:
    """Run the full ingest -> validate -> compute -> output pipeline.

    Args:
        input_path: Path to the raw CRM gift export CSV.
        output_dir: Directory to write the .hyper, .xlsx, and .pdf outputs into.
        metrics_registry_path: Path to metrics_registry.yaml.
        validation_rules_path: Path to validation_rules.yaml.
        as_of: Optional fixed "current date" for fiscal-year anchoring (tests use this).

    Returns:
        A PipelineResult with output paths and computed metrics/DQ report.

    Raises:
        PipelineError: If any stage fails with a user-actionable problem
            (missing file, bad config, missing required columns).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        with _timed_stage("Ingest"):
            df = ingest(input_path)
            logger.info("   loaded %s records", f"{len(df):,}")

        with _timed_stage("Validate"):
            dq_report = validate(df, validation_rules_path)
            logger.info(
                "   overall status=%s  critical=%d  warning=%d",
                dq_report.overall_status,
                dq_report.critical_failures,
                dq_report.warning_failures,
            )

        with _timed_stage("Compute metrics"):
            metrics = compute(df, metrics_registry_path, as_of=as_of)
            for m in metrics:
                logger.info("   %s = %s %s", m.id, m.value, m.unit)

        with _timed_stage("Write Tableau .hyper extract"):
            hyper_path = write_hyper(df, output_dir / "carmari_academy_extract.hyper")

        with _timed_stage("Write Excel executive report"):
            excel_path = generate_excel_report(metrics, dq_report, output_dir / "executive_report.xlsx")

        with _timed_stage("Write PDF executive summary"):
            pdf_path = generate_pdf_report(metrics, dq_report, output_dir / "executive_summary.pdf")

    except (IngestError, ValidationConfigError, MetricsConfigError) as exc:
        raise PipelineError(str(exc)) from exc

    return PipelineResult(
        hyper_path=hyper_path,
        excel_path=excel_path,
        pdf_path=pdf_path,
        metrics=metrics,
        dq_report=dq_report,
        record_count=len(df),
    )


@click.group()
def cli() -> None:
    """Advancement Analytics Pipeline CLI."""


@cli.command()
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the raw CRM gift export CSV.",
)
@click.option(
    "--output-dir",
    default="output",
    type=click.Path(file_okay=False),
    help="Directory to write pipeline outputs into.",
)
def run(input_path: str, output_dir: str) -> None:
    """Run the full pipeline: ingest -> validate -> compute -> write outputs."""
    try:
        result = run_pipeline(input_path, output_dir)
    except PipelineError as exc:
        click.echo(f"Pipeline failed: {exc}", err=True)
        sys.exit(1)

    click.echo("")
    click.echo(f"Pipeline complete. {result.record_count:,} records processed.")
    click.echo(f"  Data quality: {result.dq_report.overall_status.upper()}")
    click.echo(f"  Tableau extract: {result.hyper_path}")
    click.echo(f"  Excel report:    {result.excel_path}")
    click.echo(f"  PDF summary:     {result.pdf_path}")


if __name__ == "__main__":
    cli()
