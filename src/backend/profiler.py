"""Ingestion and profiling. Everything here runs locally; nothing here may
import a networking library.

``profile_dataframe`` is where a privacy breach would originate: a profile is
the one object the frontend may forward to an LLM. Two rules are enforced in
code, not by convention:

1. **No cell values.** Detectors report *that* a pattern exists, never *which*
   value shows it. Issue codes carry the observation; example values never
   leave this module.
2. **No aggregates on identifier-like numerics.** ``min``/``max`` of an
   ``order_id`` or a phone number stored as digits are real records. Numeric
   aggregates are emitted only for numeric columns that look like measures.

Every statistic is computed with vectorised Polars expressions - no Python
iteration over values.
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import polars as pl
from loguru import logger

from src.contracts import ColumnProfile, DataProfile, NumericAggregates

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

HIGH_NULL_THRESHOLD_PCT: float = 20.0
MIXED_NUMERIC_MIN_FRACTION: float = 0.20
DATE_COVERAGE_MIN_FRACTION: float = 0.50
OUTLIER_IQR_MULTIPLIER: float = 3.0
OUTLIER_MIN_ROWS: int = 8
IDENTIFIER_UNIQUE_RATIO: float = 0.98
IDENTIFIER_MIN_ROWS: int = 10

#: Column-name patterns that mark identifiers or sensitive numerics. Matched
#: case-insensitively against the whole name or a trailing token.
_IDENTIFIER_NAME = re.compile(
    r"(?i)(?:(?:^|[_\s-])(?:id|uid|uuid|key|code|no|num|number|ref|reference|"
    r"phone|mobile|tel|msisdn|card|pan|account|acct|iban|ssn|nin|pin|passport|"
    r"license|licence|plate)(?:$|[_\s-])|^id|number$|phone$|pan$)"
)

#: Date shapes the detector recognises. Anchored; applied via Polars regex.
_DATE_SHAPES: dict[str, str] = {
    "iso": r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$",
    "slash_dmy_or_mdy": r"^\d{1,2}/\d{1,2}/\d{4}$",
    "dash_dmy": r"^\d{1,2}-\d{1,2}-\d{4}$",
    "dot_dmy": r"^\d{1,2}\.\d{1,2}\.\d{4}$",
    "slash_ymd": r"^\d{4}/\d{2}/\d{2}$",
    "day_month_name": r"^\d{1,2} [A-Za-z]{3,9},? \d{4}$",
    "month_name_day": r"^[A-Za-z]{3,9} \d{1,2},? \d{4}$",
}


class DatasetLoadError(RuntimeError):
    """The file could not be read as a table. Wraps the reader's error."""


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


def load_dataset(source: str | Path | bytes | BinaryIO, *, filename: str | None = None) -> pl.DataFrame:
    """Read a ``.csv`` or ``.xlsx`` into a DataFrame.

    Accepts a path, raw bytes, or a binary file-like (a Streamlit upload's
    buffer). When ``source`` is not a path, ``filename`` supplies the
    extension. Schema inference is permissive: a messy file must not raise
    on content, only on structure.
    """
    name = filename or (str(source) if isinstance(source, (str, Path)) else "")
    ext = Path(name).suffix.lower()
    if ext not in {".csv", ".xlsx"}:
        raise DatasetLoadError(f"unsupported file type {ext or '(none)'}; expected .csv or .xlsx")

    data: str | Path | io.BytesIO
    if isinstance(source, (str, Path)):
        data = Path(source)
        if not data.exists():
            raise DatasetLoadError(f"file not found: {data}")
    elif isinstance(source, (bytes, bytearray)):
        data = io.BytesIO(source)
    else:
        raw = source.read()
        data = io.BytesIO(raw if isinstance(raw, bytes) else raw.encode("utf-8"))

    try:
        if ext == ".csv":
            df = pl.read_csv(
                data,
                infer_schema_length=10_000,
                ignore_errors=True,
                try_parse_dates=False,  # dates stay strings so the detector can see mixed formats
                truncate_ragged_lines=True,
            )
        else:
            df = pl.read_excel(data, engine="openpyxl")
    except Exception as exc:  # noqa: BLE001 - normalise every reader error
        raise DatasetLoadError(f"could not read {name or 'upload'}: {exc}") from exc

    logger.info("profiler: loaded {} - {} rows x {} cols", name or "upload", df.height, df.width)
    return df


# --------------------------------------------------------------------------- #
# Profiling
# --------------------------------------------------------------------------- #


def profile_dataframe(df: pl.DataFrame) -> DataProfile:
    """Aggregate statistics only. See module docstring for what is withheld."""
    total_rows = df.height
    exact_dupes = (total_rows - df.unique(maintain_order=False).height) if total_rows else 0

    columns: list[ColumnProfile] = [_profile_column(df, name) for name in df.columns]

    profile: DataProfile = {
        "total_rows": total_rows,
        "total_columns": df.width,
        "exact_duplicate_count": int(exact_dupes),
        "profiled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "columns": columns,
    }
    logger.info(
        "profiler: {} rows, {} cols, {} exact duplicates, {} column(s) with issues",
        total_rows,
        df.width,
        exact_dupes,
        sum(1 for c in columns if c["issues_detected"]),
    )
    return profile


def _profile_column(df: pl.DataFrame, name: str) -> ColumnProfile:
    s = df.get_column(name)
    dtype = s.dtype
    total = s.len()
    null_count = int(s.null_count())
    non_null = total - null_count
    unique_count = int(s.drop_nulls().n_unique()) if non_null else 0
    numeric = dtype.is_numeric()

    col: ColumnProfile = {
        "name": name,
        "dtype": str(dtype),
        "null_count": null_count,
        "null_pct": round(null_count / total * 100.0, 2) if total else 0.0,
        "unique_count": unique_count,
        "is_numeric": numeric,
        "issues_detected": [],
    }

    identifier_like = is_identifier_like(name, s, unique_count=unique_count, non_null=non_null)

    if numeric and non_null and not identifier_like:
        col["numeric"] = _numeric_aggregates(s)

    issues: list[str] = []
    if col["null_pct"] >= HIGH_NULL_THRESHOLD_PCT:
        issues.append("high_null_rate")
    if identifier_like and non_null and unique_count < non_null:
        issues.append("possible_duplicate_key")
    if numeric and not identifier_like and non_null >= OUTLIER_MIN_ROWS and _has_outliers(s):
        issues.append("numeric_outliers")
    if dtype == pl.String and non_null:
        issues.extend(_string_issues(df, name, non_null))

    col["issues_detected"] = issues
    return col


def is_identifier_like(name: str, s: pl.Series, *, unique_count: int, non_null: int) -> bool:
    """Name says identifier, or it's an integer column that is (almost) all
    unique - either way its min/max would be real values, so withhold them."""
    if _IDENTIFIER_NAME.search(name):
        return True
    if s.dtype.is_integer() and non_null >= IDENTIFIER_MIN_ROWS:
        return unique_count / non_null >= IDENTIFIER_UNIQUE_RATIO
    return False


def _numeric_aggregates(s: pl.Series) -> NumericAggregates:
    v = s.drop_nulls().cast(pl.Float64)
    std = v.std()
    return {
        "min": float(v.min()),
        "max": float(v.max()),
        "mean": round(float(v.mean()), 6),
        "median": float(v.median()),
        "std": round(float(std), 6) if std is not None else 0.0,
    }


def _has_outliers(s: pl.Series) -> bool:
    v = s.drop_nulls().cast(pl.Float64)
    q1, q3 = v.quantile(0.25, interpolation="linear"), v.quantile(0.75, interpolation="linear")
    if q1 is None or q3 is None:
        return False
    iqr = q3 - q1
    if iqr <= 0:
        return False
    lo, hi = q1 - OUTLIER_IQR_MULTIPLIER * iqr, q3 + OUTLIER_IQR_MULTIPLIER * iqr
    return bool(((v < lo) | (v > hi)).any())


def _string_issues(df: pl.DataFrame, name: str, non_null: int) -> list[str]:
    """All detectors for one string column in a single lazy pass.

    Each result is a count or a boolean. No value is materialised into Python.
    """
    c = pl.col(name)
    stripped = c.str.strip_chars()

    exprs = [
        (c != stripped).sum().alias("ws"),
        stripped.n_unique().alias("nu_raw"),
        stripped.str.to_lowercase().n_unique().alias("nu_lower"),
        stripped.cast(pl.Float64, strict=False).is_not_null().sum().alias("numeric_like"),
    ]
    exprs += [stripped.str.contains(pattern).sum().alias(f"date_{key}") for key, pattern in _DATE_SHAPES.items()]

    row = df.lazy().select(exprs).collect().row(0, named=True)

    issues: list[str] = []
    if row["ws"] and row["ws"] > 0:
        issues.append("leading_trailing_whitespace")
    if (row["nu_raw"] or 0) > 1 and (row["nu_lower"] or 0) < (row["nu_raw"] or 0):
        issues.append("inconsistent_casing")

    numeric_like = row["numeric_like"] or 0
    if non_null >= 5 and MIXED_NUMERIC_MIN_FRACTION <= numeric_like / non_null < 1.0:
        issues.append("mixed_numeric_and_text")

    date_hits = {k: (row[f"date_{k}"] or 0) for k in _DATE_SHAPES}
    shapes_present = sum(1 for n in date_hits.values() if n > 0)
    coverage = sum(date_hits.values()) / non_null
    if shapes_present >= 2 and coverage >= DATE_COVERAGE_MIN_FRACTION:
        issues.append("mixed_date_formats")

    return issues


__all__ = ["DatasetLoadError", "is_identifier_like", "load_dataset", "profile_dataframe"]
