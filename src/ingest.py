"""Ingestion stage: load a raw CRM gift export and standardize it for downstream use.

Handles the realistic messiness of a raw export -- inconsistent column
naming, mixed date formats, stray whitespace -- and fails fast with a clear
error message if the file is missing required columns, rather than letting
a cryptic exception surface deep in the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["donor_id", "gift_amount", "gift_date"]

# Maps common raw-export column spellings to this pipeline's canonical names.
COLUMN_ALIASES = {
    "donorid": "donor_id",
    "donor id": "donor_id",
    "giftid": "gift_id",
    "gift id": "gift_id",
    "giftamount": "gift_amount",
    "gift amount": "gift_amount",
    "amount": "gift_amount",
    "giftdate": "gift_date",
    "gift date": "gift_date",
    "date": "gift_date",
    "fund": "fund_designation",
    "funddesignation": "fund_designation",
    "fund designation": "fund_designation",
    "constituenttype": "constituent_type",
    "constituent type": "constituent_type",
    "classyear": "class_year",
    "class year": "class_year",
    "firstname": "first_name",
    "first name": "first_name",
    "lastname": "last_name",
    "last name": "last_name",
    "capacitytier": "capacity_tier",
    "capacity tier": "capacity_tier",
    "givingcapacity": "capacity_tier",
}

STRING_COLUMNS = [
    "donor_id",
    "gift_id",
    "first_name",
    "last_name",
    "email",
    "phone",
    "constituent_type",
    "capacity_tier",
    "city",
    "state",
    "fund_designation",
]


class IngestError(Exception):
    """Raised when the input file cannot be ingested (missing file or required columns)."""


def _normalize_column_name(raw_name: str) -> str:
    """Map a raw column header to its canonical snake_case pipeline name."""
    key = raw_name.strip().lower()
    if key in COLUMN_ALIASES:
        return COLUMN_ALIASES[key]
    return key.replace(" ", "_")


def load_raw_csv(input_path: str | Path) -> pd.DataFrame:
    """Read the raw CRM export CSV into a DataFrame without transforming values.

    Args:
        input_path: Path to the raw CSV export.

    Returns:
        The raw DataFrame with only column names normalized.

    Raises:
        IngestError: If the file does not exist or cannot be parsed as CSV.
    """
    path = Path(input_path)
    if not path.exists():
        raise IngestError(f"Input file not found: {path}")

    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception as exc:  # pragma: no cover - pandas raises many exception types
        raise IngestError(f"Failed to parse '{path}' as CSV: {exc}") from exc

    df.columns = [_normalize_column_name(c) for c in df.columns]
    return df


def _parse_gift_date(value: str) -> pd.Timestamp | None:
    """Parse a gift_date string that may be in any of several common export formats."""
    value = value.strip()
    if not value:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed


def standardize(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and type-normalize a raw, column-normalized DataFrame.

    - Validates that all required columns are present.
    - Strips whitespace from string columns.
    - Coerces gift_amount to float (invalid values become NaN).
    - Parses gift_date from mixed formats into datetime (invalid values become NaT).
    - Coerces class_year to a nullable integer.

    Args:
        df: Output of `load_raw_csv`.

    Returns:
        A standardized DataFrame ready for validation and metrics computation.

    Raises:
        IngestError: If any required column is missing.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise IngestError(
            f"Input file is missing required column(s): {', '.join(missing)}. "
            f"Found columns: {', '.join(df.columns)}"
        )

    out = df.copy()

    for col in STRING_COLUMNS:
        if col in out.columns:
            out[col] = out[col].astype(str).str.strip()
            out.loc[out[col].isin(["", "nan", "None"]), col] = pd.NA

    out["gift_amount"] = pd.to_numeric(out["gift_amount"], errors="coerce")
    out["gift_date"] = out["gift_date"].astype(str).apply(_parse_gift_date)

    if "class_year" in out.columns:
        out["class_year"] = pd.to_numeric(out["class_year"], errors="coerce").astype("Int64")

    return out


def ingest(input_path: str | Path) -> pd.DataFrame:
    """Load and standardize a raw CRM gift export in one step.

    Args:
        input_path: Path to the raw CSV export.

    Returns:
        A standardized DataFrame ready for the validation stage.
    """
    raw = load_raw_csv(input_path)
    return standardize(raw)
