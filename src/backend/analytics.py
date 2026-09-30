"""DuckDB analytics over a Polars frame. Read-only by construction.

Two ways in:

- ``run_query`` - a caller-supplied ``SELECT``. Guarded: single statement,
  must start with SELECT/WITH, no mutating keywords, DuckDB external access
  disabled so ``read_csv('/etc/passwd')`` and friends are refused. The frame
  is registered as a view over in-memory Arrow data; nothing is written.
- ``aggregate`` / ``value_counts`` / ``numeric_summary`` - built SQL. Column
  names are validated against ``df.columns`` and then quoted; values travel
  as bound parameters. No user or model string is ever interpolated.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator

import duckdb
import polars as pl
from loguru import logger

TABLE: str = "dataset"

_READ_ONLY_START = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)
_MUTATING = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|copy|attach|detach|"
    r"install|load|pragma|call|export|import|set|reset|vacuum|checkpoint|begin|commit|rollback)\b",
    re.IGNORECASE,
)
_AGGS: frozenset[str] = frozenset({"sum", "avg", "count", "min", "max", "median"})


class AnalyticsError(ValueError):
    """Rejected query or DuckDB execution failure."""


@contextmanager
def _connection(df: pl.DataFrame) -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")  # a query can no longer flip it back
        # Register a pyarrow Table, NOT the Polars frame. Handing DuckDB the frame
        # directly faults (0xC0000005) on duckdb 1.5.5 / polars 1.44 / Python 3.14
        # for frames with nulls in string columns; bisected 2026-09-20, spec 7.1.
        # The oldest Arrow compat level avoids string_view buffers entirely.
        con.register(TABLE, df.to_arrow(compat_level=pl.CompatLevel.oldest()))
        yield con
    finally:
        con.close()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _check_columns(df: pl.DataFrame, *columns: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise AnalyticsError(f"column(s) not found: {missing}")


# --------------------------------------------------------------------------- #
# Free-form (guarded)
# --------------------------------------------------------------------------- #


def run_query(df: pl.DataFrame, sql: str) -> pl.DataFrame:
    """Run one read-only SELECT against ``df`` exposed as table ``dataset``."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise AnalyticsError("empty query")
    if ";" in text:
        raise AnalyticsError("only a single statement is allowed")
    if not _READ_ONLY_START.match(text):
        raise AnalyticsError("only SELECT / WITH queries are allowed")
    hit = _MUTATING.search(text)
    if hit:
        raise AnalyticsError(f"keyword {hit.group(1).upper()!r} is not allowed in analytics queries")

    with _connection(df) as con:
        try:
            result = con.execute(text).pl()
        except duckdb.Error as exc:
            raise AnalyticsError(str(exc)) from exc
    logger.debug("analytics: query returned {} rows x {} cols", result.height, result.width)
    return result


# --------------------------------------------------------------------------- #
# Built queries
# --------------------------------------------------------------------------- #


def aggregate(
    df: pl.DataFrame,
    group_by: str,
    measure: str,
    agg: str = "sum",
    *,
    top_n: int | None = 50,
) -> pl.DataFrame:
    """``SELECT group_by, agg(measure) FROM dataset GROUP BY group_by``,
    ordered by the aggregate descending. Columns: ``group_by`` and ``value``."""
    _check_columns(df, group_by, measure)
    if agg not in _AGGS:
        raise AnalyticsError(f"agg must be one of {sorted(_AGGS)}, got {agg!r}")
    if agg != "count" and not df.schema[measure].is_numeric():
        raise AnalyticsError(f"{agg} requires a numeric measure; {measure!r} is {df.schema[measure]}")

    sql = (
        f"SELECT {_quote(group_by)} AS {_quote(group_by)}, {agg}({_quote(measure)}) AS value "
        f"FROM {TABLE} GROUP BY 1 ORDER BY value DESC NULLS LAST"
    )
    params: list[object] = []
    if top_n is not None:
        sql += " LIMIT ?"
        params.append(int(top_n))
    with _connection(df) as con:
        return con.execute(sql, params).pl()


def value_counts(df: pl.DataFrame, column: str, *, top_n: int = 20) -> pl.DataFrame:
    """Distinct values of ``column`` with counts, most common first.
    Local-only output: this is for the user's screen, never for an LLM."""
    _check_columns(df, column)
    sql = (
        f"SELECT {_quote(column)} AS value, count(*) AS count FROM {TABLE} "
        f"GROUP BY 1 ORDER BY count DESC, 1 LIMIT ?"
    )
    with _connection(df) as con:
        return con.execute(sql, [int(top_n)]).pl()


def numeric_summary(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """count / nulls / min / q1 / median / q3 / max / mean / std for one numeric column."""
    _check_columns(df, column)
    if not df.schema[column].is_numeric():
        raise AnalyticsError(f"{column!r} is not numeric")
    q = _quote(column)
    sql = (
        f"SELECT count({q}) AS count, count(*) - count({q}) AS nulls, "
        f"min({q}) AS min, quantile_cont({q}, 0.25) AS q1, median({q}) AS median, "
        f"quantile_cont({q}, 0.75) AS q3, max({q}) AS max, avg({q}) AS mean, stddev_samp({q}) AS std "
        f"FROM {TABLE}"
    )
    with _connection(df) as con:
        return con.execute(sql).pl()


def timeseries(
    df: pl.DataFrame,
    date_column: str,
    measure: str,
    *,
    grain: str = "month",
    agg: str = "sum",
) -> pl.DataFrame:
    """Aggregate ``measure`` per ``grain`` of ``date_column``. The date column
    may be a Date/Datetime or an ISO-8601 string (as ``standardize_dates``
    emits). Columns: ``period`` and ``value``."""
    _check_columns(df, date_column, measure)
    if grain not in {"day", "week", "month", "quarter", "year"}:
        raise AnalyticsError(f"grain must be day/week/month/quarter/year, got {grain!r}")
    if agg not in _AGGS:
        raise AnalyticsError(f"agg must be one of {sorted(_AGGS)}, got {agg!r}")
    d = _quote(date_column)
    cast = f"TRY_CAST({d} AS TIMESTAMP)" if df.schema[date_column] == pl.String else d
    sql = (
        f"SELECT date_trunc(?, {cast}) AS period, {agg}({_quote(measure)}) AS value "
        f"FROM {TABLE} WHERE {cast} IS NOT NULL GROUP BY 1 ORDER BY 1"
    )
    with _connection(df) as con:
        return con.execute(sql, [grain]).pl()


__all__ = ["AnalyticsError", "TABLE", "aggregate", "numeric_summary", "run_query", "timeseries", "value_counts"]
