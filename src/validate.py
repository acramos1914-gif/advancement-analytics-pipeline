"""Validation stage: apply governed business rules to an ingested dataset.

Rules are defined in config/validation_rules.yaml rather than hard-coded, so
data stewards can add or adjust a rule without touching this module. Every
rule is evaluated independently -- one failing rule never prevents the rest
from running -- so a single pass produces the full data-quality picture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


class ValidationConfigError(Exception):
    """Raised when validation_rules.yaml is missing, malformed, or references an unknown column."""


@dataclass
class RuleResult:
    """Outcome of evaluating a single validation rule against the dataset."""

    rule_id: str
    description: str
    severity: str
    status: str  # "pass" or "fail"
    records_affected: int
    total_records: int
    sample_ids: list[str] = field(default_factory=list)

    @property
    def pct_affected(self) -> float:
        """Percentage of records affected by this rule, 0-100."""
        if self.total_records == 0:
            return 0.0
        return round(100 * self.records_affected / self.total_records, 2)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this result to a plain dict for reporting/output."""
        return {
            "rule_id": self.rule_id,
            "description": self.description,
            "severity": self.severity,
            "status": self.status,
            "records_affected": self.records_affected,
            "total_records": self.total_records,
            "pct_affected": self.pct_affected,
            "sample_ids": self.sample_ids,
        }


@dataclass
class DataQualityReport:
    """The full set of rule results for one validation run."""

    rule_results: list[RuleResult]
    total_records: int

    @property
    def critical_failures(self) -> int:
        """Count of rules with severity=critical that failed."""
        return sum(1 for r in self.rule_results if r.severity == "critical" and r.status == "fail")

    @property
    def warning_failures(self) -> int:
        """Count of rules with severity=warning that failed."""
        return sum(1 for r in self.rule_results if r.severity == "warning" and r.status == "fail")

    @property
    def overall_status(self) -> str:
        """Overall dataset status: fail if any critical rule fails, else warn/pass."""
        if self.critical_failures > 0:
            return "fail"
        if self.warning_failures > 0:
            return "warn"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full report to a plain dict for reporting/output."""
        return {
            "total_records": self.total_records,
            "overall_status": self.overall_status,
            "critical_failures": self.critical_failures,
            "warning_failures": self.warning_failures,
            "rules": [r.to_dict() for r in self.rule_results],
        }


def load_rules(rules_path: str | Path) -> list[dict[str, Any]]:
    """Load and parse the validation rule definitions.

    Args:
        rules_path: Path to validation_rules.yaml.

    Returns:
        A list of rule dicts as defined in the YAML file.

    Raises:
        ValidationConfigError: If the file is missing or malformed.
    """
    path = Path(rules_path)
    if not path.exists():
        raise ValidationConfigError(f"Validation rules file not found: {path}")

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "rules" not in data:
        raise ValidationConfigError(f"'{path}' does not contain a top-level 'rules' list.")

    return data["rules"]


def _check_required_field(df: pd.DataFrame, column: str) -> pd.Series:
    """Return a boolean mask of rows where `column` is missing."""
    if column not in df.columns:
        return pd.Series(True, index=df.index)
    return df[column].isna()


def _check_format(df: pd.DataFrame, rule: dict[str, Any]) -> pd.Series:
    """Return a boolean mask of rows that fail a format rule (regex pattern or future-date check)."""
    column = rule["column"]
    if column not in df.columns:
        return pd.Series(True, index=df.index)

    if rule["id"] == "valid_gift_date_not_future":
        today = pd.Timestamp(date.today())
        return df[column].notna() & (df[column] > today)

    pattern = rule.get("pattern")
    if not pattern:
        return pd.Series(False, index=df.index)

    present = df[column].notna()
    matches = df[column].astype(str).str.match(pattern)
    return present & ~matches


def _check_range(df: pd.DataFrame, rule: dict[str, Any]) -> pd.Series:
    """Return a boolean mask of rows that fall outside an allowed numeric range."""
    column = rule["column"]
    if column not in df.columns:
        return pd.Series(True, index=df.index)

    values = pd.to_numeric(df[column], errors="coerce")
    present = values.notna()

    min_v = rule.get("min")
    max_v = rule.get("max")
    offset = rule.get("max_offset_from_current_year")
    if offset is not None:
        max_v = date.today().year + offset

    mask = pd.Series(False, index=df.index)
    if min_v is not None:
        mask = mask | (present & (values < min_v))
    if max_v is not None:
        mask = mask | (present & (values > max_v))
    return mask


def _check_uniqueness(df: pd.DataFrame, rule: dict[str, Any]) -> pd.Series:
    """Flag all rows sharing a donor_id whose associated name fields are inconsistent.

    In a gift-level export the same donor_id legitimately repeats once per gift, so
    plain duplicate-row detection isn't useful. Instead this flags donor_ids whose
    first_name/last_name spelling or casing is inconsistent across rows -- a signal
    of a records-merge problem in the source CRM.
    """
    column = rule["column"]
    if column not in df.columns or "first_name" not in df.columns or "last_name" not in df.columns:
        return pd.Series(False, index=df.index)

    key = df["first_name"].fillna("").astype(str) + "|" + df["last_name"].fillna("").astype(str)
    distinct_counts = key.groupby(df[column]).nunique()
    conflicted_ids = distinct_counts[distinct_counts > 1].index
    return df[column].isin(conflicted_ids)


def _sample_ids(df: pd.DataFrame, mask: pd.Series, limit: int = 10) -> list[str]:
    """Return up to `limit` identifiers (donor_id preferred, else gift_id) for affected rows."""
    id_col = "donor_id" if "donor_id" in df.columns else ("gift_id" if "gift_id" in df.columns else None)
    if id_col is None:
        return []
    ids = df.loc[mask, id_col].dropna().astype(str).unique().tolist()
    return ids[:limit]


def run_validation(df: pd.DataFrame, rules: list[dict[str, Any]]) -> DataQualityReport:
    """Evaluate every validation rule against the dataset.

    Args:
        df: A standardized DataFrame, as produced by `src.ingest.ingest`.
        rules: Rule definitions, as returned by `load_rules`.

    Returns:
        A DataQualityReport summarizing pass/fail status per rule.
    """
    total = len(df)
    results: list[RuleResult] = []

    for rule in rules:
        rule_type = rule["type"]
        column = rule["column"]

        if rule_type == "required_field":
            mask = _check_required_field(df, column)
        elif rule_type == "format":
            mask = _check_format(df, rule)
        elif rule_type == "range":
            mask = _check_range(df, rule)
        elif rule_type == "uniqueness":
            mask = _check_uniqueness(df, rule)
        else:
            raise ValidationConfigError(f"Unknown rule type '{rule_type}' for rule '{rule['id']}'")

        affected = int(mask.sum())
        results.append(
            RuleResult(
                rule_id=rule["id"],
                description=rule["description"],
                severity=rule["severity"],
                status="fail" if affected > 0 else "pass",
                records_affected=affected,
                total_records=total,
                sample_ids=_sample_ids(df, mask),
            )
        )

    return DataQualityReport(rule_results=results, total_records=total)


def validate(df: pd.DataFrame, rules_path: str | Path) -> DataQualityReport:
    """Load rules from disk and run validation in one step.

    Args:
        df: A standardized DataFrame, as produced by `src.ingest.ingest`.
        rules_path: Path to validation_rules.yaml.

    Returns:
        A DataQualityReport summarizing pass/fail status per rule.
    """
    rules = load_rules(rules_path)
    return run_validation(df, rules)
