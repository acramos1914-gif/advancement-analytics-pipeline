"""End-to-end test: runs the full pipeline against the fixture dataset.

Asserts every output artifact is created and non-empty, and that a sample of
known KPI values match the hand-calculated fixture expectations -- this is
the test that proves the pipeline actually executes start to finish, which
is also what the CI workflow runs against the full sample dataset.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.pipeline import run_pipeline

FIXTURES_DIR = Path(__file__).parent / "fixtures"
AS_OF = date(2026, 9, 1)


def test_pipeline_produces_all_outputs(tmp_path):
    result = run_pipeline(
        input_path=FIXTURES_DIR / "fixture_gifts.csv",
        output_dir=tmp_path,
        as_of=AS_OF,
    )

    assert result.record_count == 18
    assert result.hyper_path.exists() and result.hyper_path.stat().st_size > 0
    assert result.excel_path.exists() and result.excel_path.stat().st_size > 0
    assert result.pdf_path.exists() and result.pdf_path.stat().st_size > 0


def test_pipeline_kpi_values_match_fixture_expectations(tmp_path):
    result = run_pipeline(
        input_path=FIXTURES_DIR / "fixture_gifts.csv",
        output_dir=tmp_path,
        as_of=AS_OF,
    )

    metrics_by_id = {m.id: m.value for m in result.metrics}
    assert metrics_by_id["lybunt"] == 2
    assert metrics_by_id["sybunt"] == 3
    assert metrics_by_id["new_donor_acquisition"] == 1
    assert metrics_by_id["average_gift"] == 350.0


def test_pipeline_data_quality_report_reflects_fixture_issues(tmp_path):
    result = run_pipeline(
        input_path=FIXTURES_DIR / "fixture_gifts.csv",
        output_dir=tmp_path,
        as_of=AS_OF,
    )

    assert result.dq_report.overall_status == "fail"
    assert result.dq_report.critical_failures == 6
    assert result.dq_report.total_records == 18
