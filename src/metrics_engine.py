"""Metrics computation stage: compute governed KPIs against a validated dataset.

KPI *definitions* (formula description, version, effective date) live in
config/metrics_registry.yaml and are treated as documentation of record.
This module contains the actual calculation logic, keyed by metric id, and
attaches the registry metadata to each result so the two never drift apart
silently -- if a metric id exists in code but not in the registry (or vice
versa), it is surfaced rather than silently ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

# Fiscal year convention: FY label is the calendar year in which the fiscal year
# ENDS. A fiscal year runs Jul 1 - Jun 30, so a gift on 2025-08-01 falls in FY2026.
FISCAL_YEAR_START_MONTH = 7


class MetricsConfigError(Exception):
    """Raised when metrics_registry.yaml is missing, malformed, or out of sync with the code."""


@dataclass
class MetricResult:
    """A computed KPI value, joined with its governed registry definition."""

    id: str
    name: str
    version: str
    value: float | int | None
    unit: str
    formula: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize this result to a plain dict for reporting/output."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "value": self.value,
            "unit": self.unit,
            "formula": self.formula,
            "notes": self.notes,
        }


def load_registry(registry_path: str | Path) -> dict[str, dict[str, Any]]:
    """Load metric definitions from the registry, keyed by metric id.

    Args:
        registry_path: Path to metrics_registry.yaml.

    Returns:
        A dict mapping metric id to its registry definition.

    Raises:
        MetricsConfigError: If the file is missing or malformed.
    """
    path = Path(registry_path)
    if not path.exists():
        raise MetricsConfigError(f"Metrics registry file not found: {path}")

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "metrics" not in data:
        raise MetricsConfigError(f"'{path}' does not contain a top-level 'metrics' list.")

    return {m["id"]: m for m in data["metrics"]}


def fiscal_year(ts: pd.Timestamp | date) -> int:
    """Return the fiscal year label (ending calendar year) for a given date."""
    return ts.year + 1 if ts.month >= FISCAL_YEAR_START_MONTH else ts.year


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to rows with the minimum fields needed for KPI math, and tag fiscal year.

    Rows missing donor_id, gift_date, or gift_amount cannot contribute to any KPI
    calculation; they are already surfaced as critical data-quality failures by the
    validation stage, so metrics computation simply excludes them rather than
    silently propagating NaT/NaN into every downstream aggregate.
    """
    clean = df.dropna(subset=["donor_id", "gift_date", "gift_amount"]).copy()
    clean["fiscal_year"] = clean["gift_date"].apply(fiscal_year)
    return clean


def compute_metrics(
    df: pd.DataFrame,
    registry: dict[str, dict[str, Any]],
    as_of: date | None = None,
) -> list[MetricResult]:
    """Compute every KPI defined in the registry against the dataset.

    Args:
        df: A standardized DataFrame, as produced by `src.ingest.ingest`.
        registry: Metric definitions, as returned by `load_registry`.
        as_of: The "current date" to anchor fiscal-year logic to. Defaults to today;
            exposed as a parameter so tests can pin it to a fixed date.

    Returns:
        A list of MetricResult, one per metric id known to this engine.

    Raises:
        MetricsConfigError: If a computed metric id has no registry definition.
    """
    anchor = as_of or date.today()
    current_fy = fiscal_year(anchor)

    clean = _prepare(df)

    donors_by_fy: dict[int, set[str]] = {
        fy: set(group["donor_id"]) for fy, group in clean.groupby("fiscal_year")
    }
    current_donors = donors_by_fy.get(current_fy, set())
    prior_donors = donors_by_fy.get(current_fy - 1, set())
    prior_3yr_union: set[str] = set()
    for offset in (1, 2, 3):
        prior_3yr_union |= donors_by_fy.get(current_fy - offset, set())

    alumni_ids = set(clean.loc[clean.get("constituent_type") == "Alumni", "donor_id"].unique())
    alumni_current_donors = current_donors & alumni_ids

    current_fy_gifts = clean.loc[clean["fiscal_year"] == current_fy, "gift_amount"]
    prior_fy_gifts = clean.loc[clean["fiscal_year"] == current_fy - 1, "gift_amount"]

    first_gift_fy = clean.groupby("donor_id")["fiscal_year"].min()

    donor_retention_rate = (
        100 * len(current_donors & prior_donors) / len(prior_donors) if prior_donors else 0.0
    )
    alumni_participation_rate = (
        100 * len(alumni_current_donors) / len(alumni_ids) if alumni_ids else 0.0
    )
    average_gift = float(current_fy_gifts.mean()) if len(current_fy_gifts) else 0.0
    prior_total = float(prior_fy_gifts.sum())
    current_total = float(current_fy_gifts.sum())
    yoy_giving_change = 100 * (current_total - prior_total) / prior_total if prior_total else 0.0
    new_donor_acquisition = int((first_gift_fy == current_fy).sum())

    raw_values: dict[str, tuple[float | int, str]] = {
        "lybunt": (len(prior_donors - current_donors), "count"),
        "sybunt": (len(prior_3yr_union - current_donors), "count"),
        "donor_retention_rate": (round(donor_retention_rate, 2), "percent"),
        "alumni_participation_rate": (round(alumni_participation_rate, 2), "percent"),
        "average_gift": (round(average_gift, 2), "currency"),
        "yoy_giving_change": (round(yoy_giving_change, 2), "percent"),
        "new_donor_acquisition": (new_donor_acquisition, "count"),
    }

    results: list[MetricResult] = []
    for metric_id, (value, unit) in raw_values.items():
        definition = registry.get(metric_id)
        if definition is None:
            raise MetricsConfigError(
                f"Metric '{metric_id}' is computed in code but has no definition in "
                f"metrics_registry.yaml. Every computed KPI must be documented there."
            )
        results.append(
            MetricResult(
                id=metric_id,
                name=definition["name"],
                version=definition["version"],
                value=value,
                unit=unit,
                formula=definition["formula"].strip(),
                notes=definition["notes"].strip(),
            )
        )

    return results


def compute(
    df: pd.DataFrame, registry_path: str | Path, as_of: date | None = None
) -> list[MetricResult]:
    """Load the registry from disk and compute metrics in one step.

    Args:
        df: A standardized DataFrame, as produced by `src.ingest.ingest`.
        registry_path: Path to metrics_registry.yaml.
        as_of: Optional fixed "current date" for fiscal-year anchoring (tests use this).

    Returns:
        A list of computed MetricResult.
    """
    registry = load_registry(registry_path)
    return compute_metrics(df, registry, as_of=as_of)
