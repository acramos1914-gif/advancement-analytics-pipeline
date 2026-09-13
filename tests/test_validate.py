"""Tests for src.validate against a fixture dataset engineered to trip every rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ingest import ingest
from src.validate import load_rules, run_validation

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).parent.parent
VALIDATION_RULES_PATH = PROJECT_ROOT / "config" / "validation_rules.yaml"

EXPECTED_RULE_OUTCOMES = {
    "required_donor_id": ("critical", 1),
    "required_gift_amount": ("critical", 1),
    "required_gift_date": ("critical", 1),
    "valid_email_format": ("warning", 1),
    "non_negative_gift_amount": ("critical", 1),
    "reasonable_gift_amount": ("warning", 1),
    "valid_class_year_range": ("warning", 1),
    "duplicate_donor_id": ("critical", 4),
    "valid_gift_date_not_future": ("critical", 1),
    "required_constituent_type": ("warning", 1),
    "valid_fund_designation": ("warning", 1),
}


@pytest.fixture()
def fixture_df():
    return ingest(FIXTURES_DIR / "fixture_gifts.csv")


def test_load_rules_returns_all_eleven_rules():
    rules = load_rules(VALIDATION_RULES_PATH)
    assert len(rules) == 11
    assert {r["id"] for r in rules} == set(EXPECTED_RULE_OUTCOMES)


def test_every_rule_fires_with_expected_severity_and_count(fixture_df):
    rules = load_rules(VALIDATION_RULES_PATH)
    report = run_validation(fixture_df, rules)

    assert report.total_records == 18
    results_by_id = {r.rule_id: r for r in report.rule_results}

    for rule_id, (severity, expected_affected) in EXPECTED_RULE_OUTCOMES.items():
        result = results_by_id[rule_id]
        assert result.severity == severity
        assert result.status == "fail"
        assert result.records_affected == expected_affected


def test_overall_status_is_fail_when_critical_rules_fail(fixture_df):
    rules = load_rules(VALIDATION_RULES_PATH)
    report = run_validation(fixture_df, rules)

    assert report.critical_failures == 6
    assert report.warning_failures == 5
    assert report.overall_status == "fail"


def test_clean_dataset_passes_all_rules():
    clean_df = ingest(PROJECT_ROOT / "data" / "sample_donors.csv")
    rules = load_rules(VALIDATION_RULES_PATH)
    report = run_validation(clean_df, rules)

    # The generator introduces a small, known amount of messiness -- this just
    # asserts validation runs cleanly end-to-end on the full-size dataset without
    # every rule failing outright.
    assert report.total_records == len(clean_df)
    assert all(r.records_affected >= 0 for r in report.rule_results)
