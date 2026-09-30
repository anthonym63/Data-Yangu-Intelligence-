"""Deterministic cleaning operations. One pure function per operation, plus
``apply``/``preview`` which dispatch a ``ProposedAction``.

Guarantees every operation keeps:

- **Pure.** Input frame is never mutated (Polars is immutable; we also never
  reassign into it). Same input, same output.
- **Idempotent.** Applying twice equals applying once. Tested per operation.
- **Never destroys unparseable data.** ``standardize_dates`` leaves a value it
  cannot parse exactly as it was and counts it, rather than nulling it.
- **No LLM, no network.** Nothing here decides *whether* to clean; it only
  executes what a human (or the policy layer) approved.

Each function returns ``(new_df, Delta)``. ``apply`` turns that into an
``AuditEntry`` (CLAUDE.md 3.3) and logs the before/after transition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import polars as pl
from loguru import logger

from src.backend import audit_logger
from src.contracts import CLEANING_OPERATIONS, AuditEntry, ProposedAction

# --------------------------------------------------------------------------- #
# Types
# --------------------------------------------------------------------------- #


class CleaningError(ValueError):
    """Bad operation, missing column, or a strategy that does not fit the dtype."""


@dataclass
class Delta:
    rows_before: int
    rows_after: int
    cells_changed: int = 0
    unparsed: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def rows_removed(self) -> int:
        return self.rows_before - self.rows_after


Result = tuple[pl.DataFrame, Delta]

#: Formats tried, in order, by ``standardize_dates``. Day-first variants come
#: before month-first because the target market writes dates that way; an
#: unambiguous value parses the same regardless.
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
)
_DATETIME_FORMATS = frozenset(f for f in DATE_FORMATS if "%H" in f)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _require_columns(df: pl.DataFrame, columns: list[str], *, op: str) -> None:
    if not columns:
        raise CleaningError(f"{op}: target_columns must not be empty")
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise CleaningError(f"{op}: column(s) not found: {missing}")


def _count_changed(before: pl.DataFrame, after: pl.DataFrame, columns: list[str]) -> int:
    """Null-safe count of cells whose value differs between two same-height frames."""
    if before.height != after.height or before.height == 0:
        return 0
    total = 0
    for c in columns:
        if c not in after.columns:
            continue
        a, b = after.get_column(c), before.get_column(c)
        if a.dtype != b.dtype:
            # A dtype conversion (e.g. Date -> formatted String) changes every
            # non-null cell; the values cannot be compared element-wise anyway.
            total += a.len() - a.null_count()
        else:
            total += int(a.ne_missing(b).sum())
    return total


def _string_columns_only(df: pl.DataFrame, columns: list[str], *, op: str) -> list[str]:
    bad = [c for c in columns if df.schema[c] != pl.String]
    if bad:
        raise CleaningError(f"{op}: only text columns are supported; not text: {bad}")
    return columns


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


def drop_exact_duplicates(df: pl.DataFrame, subset: list[str] | None = None) -> Result:
    """Remove rows identical across ``subset`` (default: every column). Keeps
    the first occurrence; preserves original order."""
    if subset:
        _require_columns(df, subset, op="drop_exact_duplicates")
    out = df.unique(subset=subset or None, keep="first", maintain_order=True)
    return out, Delta(df.height, out.height)


def trim_whitespace(df: pl.DataFrame, columns: list[str]) -> Result:
    _require_columns(df, columns, op="trim_whitespace")
    cols = _string_columns_only(df, columns, op="trim_whitespace")
    out = df.with_columns([pl.col(c).str.strip_chars() for c in cols])
    return out, Delta(df.height, out.height, _count_changed(df, out, cols))


def normalize_casing(df: pl.DataFrame, columns: list[str], casing: str = "lower") -> Result:
    _require_columns(df, columns, op="normalize_casing")
    cols = _string_columns_only(df, columns, op="normalize_casing")
    fn: dict[str, Callable[[pl.Expr], pl.Expr]] = {
        "lower": lambda e: e.str.to_lowercase(),
        "upper": lambda e: e.str.to_uppercase(),
        "title": lambda e: e.str.to_titlecase(),
    }
    if casing not in fn:
        raise CleaningError(f"normalize_casing: casing must be one of {sorted(fn)}, got {casing!r}")
    out = df.with_columns([fn[casing](pl.col(c)) for c in cols])
    return out, Delta(df.height, out.height, _count_changed(df, out, cols))


def standardize_dates(df: pl.DataFrame, columns: list[str], target_format: str = "%Y-%m-%d") -> Result:
    """Parse mixed date representations and re-emit them in one format.

    Values that match none of ``DATE_FORMATS`` are left untouched and counted
    in ``Delta.unparsed`` - we never turn a customer's value into a null
    because we failed to understand it. Output columns are strings in the
    target format, which makes the operation idempotent.
    """
    _require_columns(df, columns, op="standardize_dates")
    exprs: list[pl.Expr] = []
    for c in columns:
        dtype = df.schema[c]
        if dtype in (pl.Date, pl.Datetime) or isinstance(dtype, pl.Datetime):
            exprs.append(pl.col(c).dt.strftime(target_format).alias(c))
            continue
        if dtype != pl.String:
            raise CleaningError(f"standardize_dates: column {c!r} is {dtype}, expected text or date")
        parsed = _parse_dates(pl.col(c))
        exprs.append(
            pl.when(parsed.is_null() & pl.col(c).is_not_null())
            .then(pl.col(c))  # unparseable: keep original
            .otherwise(parsed.dt.strftime(target_format))
            .alias(c)
        )
    out = df.with_columns(exprs)

    unparsed = 0
    for c in columns:
        if df.schema[c] == pl.String:
            parsed_ok = df.lazy().select(_parse_dates(pl.col(c)).is_not_null().sum()).collect().item()
            non_null = df.height - df.get_column(c).null_count()
            unparsed += int(non_null - (parsed_ok or 0))

    delta = Delta(df.height, out.height, _count_changed(df, out, columns), unparsed=unparsed)
    if unparsed:
        delta.notes.append(f"{unparsed} value(s) did not match any known date format and were left unchanged")
    return out, delta


def _parse_dates(col: pl.Expr) -> pl.Expr:
    stripped = col.str.strip_chars()
    attempts = []
    for fmt in DATE_FORMATS:
        if fmt in _DATETIME_FORMATS:
            attempts.append(stripped.str.to_datetime(fmt, strict=False, exact=True, time_unit="us"))
        else:
            attempts.append(stripped.str.to_date(fmt, strict=False, exact=True).cast(pl.Datetime("us")))
    return pl.coalesce(attempts)


def impute_nulls(
    df: pl.DataFrame,
    columns: list[str],
    strategy: str = "median",
    fill_value: Any = None,
) -> Result:
    """Fill missing values. ``median``/``mean`` need numeric columns; ``mode``
    works on anything; ``constant`` needs ``fill_value``; ``drop_rows`` removes
    rows with a null in any target column."""
    _require_columns(df, columns, op="impute_nulls")

    if strategy == "drop_rows":
        out = df.drop_nulls(subset=columns)
        return out, Delta(df.height, out.height)

    exprs: list[pl.Expr] = []
    for c in columns:
        dtype = df.schema[c]
        e = pl.col(c)
        if strategy in ("median", "mean"):
            if not dtype.is_numeric():
                raise CleaningError(f"impute_nulls: {strategy} requires a numeric column; {c!r} is {dtype}")
            fill = e.median() if strategy == "median" else e.mean()
            exprs.append(e.fill_null(fill).alias(c))
        elif strategy == "mode":
            # mode() may return several values; sort so the choice is deterministic.
            exprs.append(e.fill_null(e.drop_nulls().mode().sort().first()).alias(c))
        elif strategy == "constant":
            if fill_value is None or fill_value == "":
                raise CleaningError("impute_nulls: strategy 'constant' requires a non-empty fill_value")
            exprs.append(e.fill_null(_literal_for(dtype, fill_value, column=c)).alias(c))
        else:
            raise CleaningError(
                f"impute_nulls: strategy must be one of median, mean, mode, constant, drop_rows; got {strategy!r}"
            )
    out = df.with_columns(exprs)
    return out, Delta(df.height, out.height, _count_changed(df, out, columns))


def _literal_for(dtype: pl.DataType, value: Any, *, column: str) -> pl.Expr:
    try:
        if dtype.is_integer():
            return pl.lit(int(value), dtype=dtype)
        if dtype.is_float():
            return pl.lit(float(value), dtype=dtype)
        if dtype == pl.Boolean:
            return pl.lit(str(value).strip().lower() in {"1", "true", "yes", "y"}, dtype=dtype)
        return pl.lit(str(value), dtype=pl.String)
    except (TypeError, ValueError) as exc:
        raise CleaningError(f"impute_nulls: fill_value {value!r} does not fit column {column!r} ({dtype})") from exc


def flag_outliers(
    df: pl.DataFrame,
    columns: list[str],
    method: str = "iqr",
    threshold: float = 1.5,
) -> Result:
    """Add ``<col>_outlier_flag`` (Boolean) per target column. Changes no
    existing value; a human decides what to do with the flagged rows."""
    _require_columns(df, columns, op="flag_outliers")
    if method not in ("iqr", "zscore"):
        raise CleaningError(f"flag_outliers: method must be 'iqr' or 'zscore', got {method!r}")
    if threshold <= 0:
        raise CleaningError(f"flag_outliers: threshold must be positive, got {threshold}")

    exprs: list[pl.Expr] = []
    for c in columns:
        if not df.schema[c].is_numeric():
            raise CleaningError(f"flag_outliers: column {c!r} is not numeric")
        x = pl.col(c).cast(pl.Float64)
        if method == "iqr":
            q1, q3 = x.quantile(0.25, interpolation="linear"), x.quantile(0.75, interpolation="linear")
            iqr = q3 - q1
            flag = (x < q1 - threshold * iqr) | (x > q3 + threshold * iqr)
        else:
            std = x.std()
            flag = pl.when(std > 0).then(((x - x.mean()) / std).abs() > threshold).otherwise(False)
        exprs.append(flag.fill_null(False).alias(f"{c}_outlier_flag"))
    out = df.with_columns(exprs)
    flagged = int(sum(out.get_column(f"{c}_outlier_flag").sum() for c in columns))
    delta = Delta(df.height, out.height, cells_changed=flagged)
    delta.notes.append(f"{flagged} value(s) flagged; no existing values changed")
    return out, delta


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

_OPERATIONS: dict[str, Callable[..., Result]] = {
    "drop_exact_duplicates": lambda df, cols, p: drop_exact_duplicates(df, subset=cols or None),
    "trim_whitespace": lambda df, cols, p: trim_whitespace(df, cols),
    "standardize_dates": lambda df, cols, p: standardize_dates(df, cols, target_format=str(p.get("target_format", "%Y-%m-%d"))),
    "impute_nulls": lambda df, cols, p: impute_nulls(df, cols, strategy=str(p.get("strategy", "median")), fill_value=p.get("fill_value")),
    "normalize_casing": lambda df, cols, p: normalize_casing(df, cols, casing=str(p.get("casing", "lower"))),
    "flag_outliers": lambda df, cols, p: flag_outliers(df, cols, method=str(p.get("method", "iqr")), threshold=float(p.get("threshold", 1.5))),
}
assert set(_OPERATIONS) == CLEANING_OPERATIONS, "cleaner dispatch table drifted from contracts.CLEANING_OPERATIONS"


def run(df: pl.DataFrame, action: ProposedAction) -> Result:
    """Execute one action. Raises ``CleaningError`` on anything off-contract."""
    op = action.get("operation")
    if op not in _OPERATIONS:
        raise CleaningError(f"unknown operation {op!r}; expected one of {sorted(CLEANING_OPERATIONS)}")
    cols = [str(c) for c in action.get("target_columns", [])]
    params = dict(action.get("parameters") or {})
    return _OPERATIONS[op](df, cols, params)


def preview(df: pl.DataFrame, action: ProposedAction) -> pl.DataFrame:
    """The frame as it would look after ``action``. Non-mutating; no audit entry."""
    return run(df, action)[0]


def apply(
    df: pl.DataFrame,
    action: ProposedAction,
    *,
    human_decision: str = "approved",
) -> tuple[pl.DataFrame, AuditEntry]:
    """Execute ``action`` and produce its audit entry. Logs the before/after
    transition (CLAUDE.md 3.3). Snapshot storage for rollback lives in
    ``audit_logger.AuditLog``; use that when you need to undo."""
    out, delta = run(df, action)
    entry = audit_logger.make_entry(action, delta, human_decision=human_decision)
    audit_logger.log_transition(entry)
    return out, entry


def summarize(action: ProposedAction, delta: Delta) -> str:
    """Plain-language one-liner for the audit trail."""
    op = action["operation"]
    cols = action.get("target_columns") or []
    where = f" in {', '.join(cols)}" if cols else ""
    if op == "drop_exact_duplicates":
        base = f"Removed {delta.rows_removed} exact duplicate row(s)"
    elif op == "trim_whitespace":
        base = f"Trimmed surrounding whitespace{where}; {delta.cells_changed} cell(s) changed"
    elif op == "standardize_dates":
        base = f"Standardised dates{where}; {delta.cells_changed} cell(s) reformatted"
    elif op == "impute_nulls":
        strategy = (action.get("parameters") or {}).get("strategy", "median")
        base = (
            f"Dropped {delta.rows_removed} row(s) with missing values{where}"
            if strategy == "drop_rows"
            else f"Filled {delta.cells_changed} missing value(s){where} using {strategy}"
        )
    elif op == "normalize_casing":
        base = f"Normalised letter case{where}; {delta.cells_changed} cell(s) changed"
    elif op == "flag_outliers":
        base = f"Flagged {delta.cells_changed} outlying value(s){where}"
    else:  # pragma: no cover - dispatch guarantees membership
        base = f"{op}{where}"
    if delta.notes:
        base += ". " + "; ".join(delta.notes)
    return base


__all__ = [
    "CleaningError",
    "DATE_FORMATS",
    "Delta",
    "apply",
    "drop_exact_duplicates",
    "flag_outliers",
    "impute_nulls",
    "normalize_casing",
    "preview",
    "run",
    "standardize_dates",
    "summarize",
    "trim_whitespace",
]
