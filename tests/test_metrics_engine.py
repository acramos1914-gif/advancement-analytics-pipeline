"""Tests for src.metrics_engine against a fixture dataset with hand-computed expected KPIs."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.ingest import ingest
from src.metrics_engine import compute, load_registry

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).parent.parent
METRICS_REGISTRY_PATH = PROJECT_ROOT / "config" / "metrics_registry.yaml"

# Fixed anchor date so fiscal-year math is deterministic regardless of when tests run.
AS_OF = date(2026, 9, 1)

EXPECTED_VALUES = {
    "lybunt": 2,
    "sybunt": 3,
    "donor_retention_rate": 33.33,
    "alumni_participation_rate": 66.67,
    "average_gift": 350.0,
    "yoy_giving_change": -51.72,
    "new_donor_acquisition": 1,
}


@pytest.fixture()
def fixture_df():
    return ingest(FIXTURES_DIR / "fixture_gifts.csv")


def test_load_registry_returns_all_seven_metrics():
    registry = load_registry(METRICS_REGISTRY_PATH)
    assert set(registry) == set(EXPECTED_VALUES)


def test_computed_metrics_match_hand_calculated_fixture_values(fixture_df):
    results = compute(fixture_df, METRICS_REGISTRY_PATH, as_of=AS_OF)
    values_by_id = {r.id for r in results}
    assert values_by_id == set(EXPECTED_VALUES)

    results_by_id = {r.id: r for r in results}
    for metric_id, expected in EXPECTED_VALUES.items():
        assert results_by_id[metric_id].value == expected, f"{metric_id} mismatch"


def test_every_metric_result_carries_registry_metadata(fixture_df):
    results = compute(fixture_df, METRICS_REGISTRY_PATH, as_of=AS_OF)
    for result in results:
        assert result.name
        assert result.version
        assert result.formula
        assert result.notes
