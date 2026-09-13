"""Tableau extract stage: write the cleaned dataset to a .hyper file.

Produces a single-table Tableau extract that can be opened directly in
Tableau Desktop/Server, so the governed, validated dataset -- not a raw
CRM export -- becomes the source of truth for downstream dashboards.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pandas as pd
from tableauhyperapi import (
    Connection,
    CreateMode,
    HyperProcess,
    Inserter,
    SqlType,
    TableDefinition,
    TableName,
    Telemetry,
)

TABLE_NAME = TableName("Extract", "Gifts")

# Column name -> Hyper SQL type for every column this pipeline ever produces.
# Columns not present in a given DataFrame are simply skipped.
COLUMN_TYPES: dict[str, SqlType] = {
    "gift_id": SqlType.text(),
    "donor_id": SqlType.text(),
    "first_name": SqlType.text(),
    "last_name": SqlType.text(),
    "email": SqlType.text(),
    "phone": SqlType.text(),
    "constituent_type": SqlType.text(),
    "class_year": SqlType.int(),
    "capacity_tier": SqlType.text(),
    "city": SqlType.text(),
    "state": SqlType.text(),
    "gift_date": SqlType.date(),
    "gift_amount": SqlType.double(),
    "fund_designation": SqlType.text(),
}


def _to_hyper_value(value: object, sql_type: SqlType):
    """Convert a pandas cell value to a value the Hyper Inserter will accept."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if pd.isna(value):
        return None
    if sql_type == SqlType.date():
        ts = pd.Timestamp(value)
        return date(ts.year, ts.month, ts.day)
    if sql_type == SqlType.int():
        return int(value)
    if sql_type == SqlType.double():
        return float(value)
    return str(value)


def write_hyper(df: pd.DataFrame, output_path: str | Path) -> Path:
    """Write a DataFrame to a Tableau .hyper extract.

    Args:
        df: The standardized, validated dataset.
        output_path: Destination path for the .hyper file.

    Returns:
        The path the extract was written to.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    columns_present = [c for c in COLUMN_TYPES if c in df.columns]
    table_def = TableDefinition(
        table_name=TABLE_NAME,
        columns=[TableDefinition.Column(c, COLUMN_TYPES[c]) for c in columns_present],
    )

    with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
        with Connection(
            endpoint=hyper.endpoint, database=path, create_mode=CreateMode.CREATE_AND_REPLACE
        ) as connection:
            connection.catalog.create_schema(TABLE_NAME.schema_name)
            connection.catalog.create_table(table_def)

            with Inserter(connection, table_def) as inserter:
                for row in df[columns_present].itertuples(index=False, name=None):
                    converted = [
                        _to_hyper_value(value, COLUMN_TYPES[col])
                        for col, value in zip(columns_present, row)
                    ]
                    inserter.add_row(converted)
                inserter.execute()

    return path
