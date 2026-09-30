"""Profiler: shape, detectors, and - above all - that no cell value escapes."""

from __future__ import annotations

import io
import json

import polars as pl
import pytest

from src.backend import profiler
from src.backend.profiler import DatasetLoadError, load_dataset, profile_dataframe
from src.contracts import ISSUE_CODES
from tests.conftest import CANARIES, MESSY_CSV


def _col(profile, name):
    return next(c for c in profile["columns"] if c["name"] == name)


# --------------------------------------------------------------------------- #
# Privacy - the gate
# --------------------------------------------------------------------------- #


def test_profile_contains_no_canary_values(canary_df):
    payload = json.dumps(profile_dataframe(canary_df))
    for canary in CANARIES:
        assert canary not in payload, f"{canary!r} leaked into the profile"


def test_identifier_like_numeric_gets_no_aggregates(canary_df):
    p = profile_dataframe(canary_df)
    assert "numeric" not in _col(p, "card_number")  # name says identifier
    assert "numeric" in _col(p, "amount")  # a real measure keeps its aggregates


def test_unique_integer_column_treated_as_identifier():
    df = pl.DataFrame({"ticket": list(range(1000, 1050)), "value": [1.0] * 50})
    p = profile_dataframe(df)
    assert "numeric" not in _col(p, "ticket")


def test_string_columns_never_get_numeric_block(messy_df):
    p = profile_dataframe(messy_df)
    for c in p["columns"]:
        if not c["is_numeric"]:
            assert "numeric" not in c


def test_profile_keys_are_exactly_the_contract(messy_df):
    p = profile_dataframe(messy_df)
    assert set(p) == {"total_rows", "total_columns", "exact_duplicate_count", "profiled_at", "columns"}
    allowed = {"name", "dtype", "null_count", "null_pct", "unique_count", "is_numeric", "issues_detected", "numeric"}
    for c in p["columns"]:
        assert set(c) <= allowed, c["name"]
        if "numeric" in c:
            assert set(c["numeric"]) == {"min", "max", "mean", "median", "std"}
        assert set(c["issues_detected"]) <= ISSUE_CODES


def test_profile_is_json_serializable(messy_df):
    json.dumps(profile_dataframe(messy_df))


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #


def test_totals(messy_df):
    p = profile_dataframe(messy_df)
    assert p["total_rows"] == 24
    assert p["total_columns"] == 9
    assert p["exact_duplicate_count"] == 3  # 1002 x3 -> 2 extra, 1007 x2 -> 1 extra


def test_null_stats(messy_df):
    p = profile_dataframe(messy_df)
    email = _col(p, "email")
    assert email["null_count"] == 13
    assert email["null_pct"] == pytest.approx(54.17, abs=0.01)
    assert _col(p, "order_id")["null_count"] == 0


def test_unique_count_excludes_nulls(messy_df):
    p = profile_dataframe(messy_df)
    assert _col(p, "email")["unique_count"] == 9


def test_numeric_aggregates(messy_df):
    amt = _col(profile_dataframe(messy_df), "amount_kes")
    assert amt["is_numeric"]
    agg = amt["numeric"]
    assert agg["min"] == 300.0
    assert agg["max"] == 890000.0
    assert agg["min"] <= agg["median"] <= agg["max"]
    assert agg["std"] > 0


def test_empty_dataframe():
    p = profile_dataframe(pl.DataFrame({"a": pl.Series([], dtype=pl.String), "b": pl.Series([], dtype=pl.Int64)}))
    assert p["total_rows"] == 0
    assert p["exact_duplicate_count"] == 0
    for c in p["columns"]:
        assert c["null_pct"] == 0.0 and c["issues_detected"] == [] and "numeric" not in c


def test_single_row_std_is_zero_not_error():
    p = profile_dataframe(pl.DataFrame({"amount": [5.0]}))
    assert _col(p, "amount")["numeric"]["std"] == 0.0


# --------------------------------------------------------------------------- #
# Detectors
# --------------------------------------------------------------------------- #


def test_detects_mixed_date_formats(messy_df):
    assert "mixed_date_formats" in _col(profile_dataframe(messy_df), "order_date")["issues_detected"]


def test_detects_inconsistent_casing(messy_df):
    p = profile_dataframe(messy_df)
    assert "inconsistent_casing" in _col(p, "region")["issues_detected"]
    assert "inconsistent_casing" in _col(p, "customer_name")["issues_detected"]  # Alice / alice


def test_detects_whitespace(messy_df):
    assert "leading_trailing_whitespace" in _col(profile_dataframe(messy_df), "customer_name")["issues_detected"]


def test_detects_high_null_rate(messy_df):
    p = profile_dataframe(messy_df)
    assert "high_null_rate" in _col(p, "email")["issues_detected"]
    assert "high_null_rate" in _col(p, "notes")["issues_detected"]
    assert "high_null_rate" not in _col(p, "order_id")["issues_detected"]


def test_detects_mixed_numeric_and_text(messy_df):
    assert "mixed_numeric_and_text" in _col(profile_dataframe(messy_df), "discount")["issues_detected"]


def test_detects_possible_duplicate_key(messy_df):
    assert "possible_duplicate_key" in _col(profile_dataframe(messy_df), "order_id")["issues_detected"]


def test_detects_numeric_outliers(messy_df):
    p = profile_dataframe(messy_df)
    assert "numeric_outliers" in _col(p, "amount_kes")["issues_detected"]
    assert "numeric_outliers" not in _col(p, "quantity")["issues_detected"]


def test_clean_frame_has_no_issues(clean_df):
    p = profile_dataframe(clean_df)
    for c in p["columns"]:
        assert c["issues_detected"] == [], c


def test_consistent_single_date_format_not_flagged():
    df = pl.DataFrame({"d": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]})
    assert _col(profile_dataframe(df), "d")["issues_detected"] == []


def test_identifier_name_patterns():
    s = pl.Series([1, 2, 3])
    for name in ("id", "order_id", "customer id", "phone", "PhoneNumber", "account_no", "Card-PAN", "ID"):
        assert profiler.is_identifier_like(name, s, unique_count=3, non_null=3), name
    for name in ("amount", "quantity", "price_kes", "score"):
        assert not profiler.is_identifier_like(name, s, unique_count=3, non_null=3), name


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


def test_load_csv_from_path():
    df = load_dataset(MESSY_CSV)
    assert df.height == 24 and df.width == 9
    assert df.schema["order_date"] == pl.String  # dates deliberately not auto-parsed
    assert df.schema["amount_kes"].is_float()


def test_load_csv_from_bytes_and_buffer():
    raw = MESSY_CSV.read_bytes()
    assert load_dataset(raw, filename="upload.csv").height == 24
    assert load_dataset(io.BytesIO(raw), filename="upload.csv").height == 24


def test_load_xlsx_roundtrip(tmp_path, messy_df):
    pytest.importorskip("xlsxwriter")
    path = tmp_path / "sales.xlsx"
    messy_df.write_excel(path)
    df = load_dataset(path)
    assert df.height == 24 and set(df.columns) == set(messy_df.columns)


def test_load_rejects_unknown_extension(tmp_path):
    p = tmp_path / "data.parquet"
    p.write_bytes(b"x")
    with pytest.raises(DatasetLoadError, match="unsupported"):
        load_dataset(p)


def test_load_rejects_missing_file(tmp_path):
    with pytest.raises(DatasetLoadError, match="not found"):
        load_dataset(tmp_path / "nope.csv")


def test_load_tolerates_ragged_and_garbage_rows():
    raw = b"a,b,c\n1,2,3\n4,5\nx,y,z,extra\n"
    df = load_dataset(raw, filename="ragged.csv")
    assert df.height == 3
