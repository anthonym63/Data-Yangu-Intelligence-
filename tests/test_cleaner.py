"""Cleaner: correctness, idempotency, and the never-destroy-data rule."""

from __future__ import annotations

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from src.backend import cleaner
from src.backend.cleaner import CleaningError
from tests.conftest import make_action


# --------------------------------------------------------------------------- #
# drop_exact_duplicates
# --------------------------------------------------------------------------- #


def test_drop_exact_duplicates(messy_df):
    out, d = cleaner.drop_exact_duplicates(messy_df)
    assert d.rows_before == 24 and d.rows_after == 21 and d.rows_removed == 3
    assert out.get_column("order_id").to_list()[:3] == [1001, 1002, 1003]  # order preserved


def test_drop_duplicates_on_subset(messy_df):
    out, _ = cleaner.drop_exact_duplicates(messy_df, subset=["order_id"])
    assert out.get_column("order_id").n_unique() == out.height


# --------------------------------------------------------------------------- #
# trim_whitespace
# --------------------------------------------------------------------------- #


def test_trim_whitespace(messy_df):
    out, d = cleaner.trim_whitespace(messy_df, ["customer_name"])
    names = out.get_column("customer_name").to_list()
    assert all(n == n.strip() for n in names if n is not None)
    assert d.cells_changed == 5  # " bob otieno" x3, "Carol Achieng ", "  Uma Njoki  "
    assert d.rows_after == d.rows_before


def test_trim_rejects_numeric_column(messy_df):
    with pytest.raises(CleaningError, match="text"):
        cleaner.trim_whitespace(messy_df, ["amount_kes"])


# --------------------------------------------------------------------------- #
# normalize_casing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("casing", "expected"), [("lower", "nairobi"), ("upper", "NAIROBI"), ("title", "Nairobi")])
def test_normalize_casing(messy_df, casing, expected):
    out, d = cleaner.normalize_casing(messy_df, ["region"], casing=casing)
    assert set(out.get_column("region").to_list()) <= {expected, "Mombasa", "MOMBASA", "mombasa", "Kisumu", "KISUMU",
                                                        "kisumu", "Nakuru", "NAKURU", "nakuru", "Eldoret", "ELDORET",
                                                        "eldoret", "Thika", "THIKA", "thika"}
    assert out.get_column("region").to_list()[0] == expected
    assert d.cells_changed > 0


def test_normalize_casing_rejects_bad_mode(messy_df):
    with pytest.raises(CleaningError):
        cleaner.normalize_casing(messy_df, ["region"], casing="camel")


# --------------------------------------------------------------------------- #
# standardize_dates
# --------------------------------------------------------------------------- #


def test_standardize_dates_handles_every_fixture_format(messy_df):
    out, d = cleaner.standardize_dates(messy_df, ["order_date"])
    vals = out.get_column("order_date").to_list()
    parsed = [v for v in vals if v is not None and v != "not a date"]
    assert all(len(v) == 10 and v[4] == "-" and v[7] == "-" for v in parsed), parsed
    assert vals[1] == "2024-01-15"  # 15/01/2024
    assert vals[2] == "2024-01-16"  # 16-01-2024
    assert vals[3] == "2024-01-17"  # 17 Jan 2024
    assert vals[4] == "2024-01-18"  # 2024/01/18
    assert vals[5] == "2024-01-19"  # 19.01.2024
    assert vals[8] == "2024-01-22"  # Jan 22, 2024


def test_standardize_dates_preserves_unparseable_and_nulls(messy_df):
    out, d = cleaner.standardize_dates(messy_df, ["order_date"])
    vals = out.get_column("order_date").to_list()
    assert vals[21] == "not a date"  # kept verbatim, never nulled
    assert vals[22] is None  # null stays null
    assert d.unparsed == 1
    assert d.notes and "left unchanged" in d.notes[0]


def test_standardize_dates_datetime_target():
    df = pl.DataFrame({"d": ["2024-01-15", "15/01/2024 "]})
    out, _ = cleaner.standardize_dates(df, ["d"], target_format="%Y-%m-%dT%H:%M:%S")
    assert out.get_column("d").to_list() == ["2024-01-15T00:00:00", "2024-01-15T00:00:00"]


def test_standardize_dates_on_real_date_dtype():
    import datetime as dt

    df = pl.DataFrame({"d": [dt.date(2024, 1, 15), None], "t": [dt.datetime(2024, 1, 15, 9, 30), None]})
    out, _ = cleaner.standardize_dates(df, ["d", "t"])
    assert out.get_column("d").to_list() == ["2024-01-15", None]
    assert out.get_column("t").to_list() == ["2024-01-15", None]


def test_standardize_dates_rejects_numeric(messy_df):
    with pytest.raises(CleaningError):
        cleaner.standardize_dates(messy_df, ["quantity"])


# --------------------------------------------------------------------------- #
# impute_nulls
# --------------------------------------------------------------------------- #


def test_impute_median(messy_df):
    out, d = cleaner.impute_nulls(messy_df, ["amount_kes"], strategy="median")
    assert out.get_column("amount_kes").null_count() == 0
    assert d.cells_changed == 1
    assert out.get_column("amount_kes").to_list()[22] == messy_df.get_column("amount_kes").median()


def test_impute_mode_on_text(messy_df):
    out, d = cleaner.impute_nulls(messy_df, ["email"], strategy="mode")
    assert out.get_column("email").null_count() == 0
    assert d.cells_changed == 13


def test_impute_constant_casts_to_column_dtype(messy_df):
    out, _ = cleaner.impute_nulls(messy_df, ["amount_kes"], strategy="constant", fill_value="0")
    assert out.schema["amount_kes"].is_float()
    assert out.get_column("amount_kes").null_count() == 0


def test_impute_constant_requires_value(messy_df):
    with pytest.raises(CleaningError, match="fill_value"):
        cleaner.impute_nulls(messy_df, ["email"], strategy="constant", fill_value="")


def test_impute_constant_rejects_unfit_value(messy_df):
    with pytest.raises(CleaningError, match="does not fit"):
        cleaner.impute_nulls(messy_df, ["quantity"], strategy="constant", fill_value="lots")


def test_impute_median_rejects_text(messy_df):
    with pytest.raises(CleaningError, match="numeric"):
        cleaner.impute_nulls(messy_df, ["email"], strategy="median")


def test_impute_drop_rows(messy_df):
    out, d = cleaner.impute_nulls(messy_df, ["order_date", "amount_kes"], strategy="drop_rows")
    assert d.rows_removed == 1  # only row 1020 has a null in either
    assert out.height == 23


def test_impute_unknown_strategy(messy_df):
    with pytest.raises(CleaningError, match="strategy"):
        cleaner.impute_nulls(messy_df, ["email"], strategy="magic")


# --------------------------------------------------------------------------- #
# flag_outliers
# --------------------------------------------------------------------------- #


def test_flag_outliers_iqr(messy_df):
    out, d = cleaner.flag_outliers(messy_df, ["amount_kes"], method="iqr", threshold=1.5)
    flags = out.get_column("amount_kes_outlier_flag")
    assert flags.dtype == pl.Boolean and flags.null_count() == 0
    flagged_amounts = out.filter(pl.col("amount_kes_outlier_flag")).get_column("amount_kes").to_list()
    assert 890000.0 in flagged_amounts
    assert d.cells_changed == len(flagged_amounts)
    assert_frame_equal(out.drop("amount_kes_outlier_flag"), messy_df)  # nothing else touched


def test_flag_outliers_zscore(messy_df):
    out, _ = cleaner.flag_outliers(messy_df, ["amount_kes"], method="zscore", threshold=2.5)
    assert out.filter(pl.col("amount_kes_outlier_flag")).get_column("amount_kes").to_list() == [890000.0, 890000.0]


def test_flag_outliers_constant_column_flags_nothing():
    df = pl.DataFrame({"v": [5.0] * 10})
    out, d = cleaner.flag_outliers(df, ["v"], method="zscore")
    assert d.cells_changed == 0 and not out.get_column("v_outlier_flag").any()


def test_flag_outliers_rejects_text_and_bad_args(messy_df):
    with pytest.raises(CleaningError):
        cleaner.flag_outliers(messy_df, ["email"])
    with pytest.raises(CleaningError):
        cleaner.flag_outliers(messy_df, ["amount_kes"], method="mad")
    with pytest.raises(CleaningError):
        cleaner.flag_outliers(messy_df, ["amount_kes"], threshold=0)


# --------------------------------------------------------------------------- #
# Idempotency - applying twice equals applying once, for every operation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "action",
    [
        make_action("drop_exact_duplicates"),
        make_action("trim_whitespace", ["customer_name"]),
        make_action("standardize_dates", ["order_date"]),
        make_action("impute_nulls", ["amount_kes"], strategy="median"),
        make_action("impute_nulls", ["email"], strategy="mode"),
        make_action("impute_nulls", ["notes"], strategy="constant", fill_value="-"),
        make_action("normalize_casing", ["region"], casing="title"),
        make_action("flag_outliers", ["amount_kes"], method="iqr", threshold=3.0),
    ],
    ids=lambda a: f"{a['operation']}:{a['parameters']}",
)
def test_operations_are_idempotent(messy_df, action):
    once = cleaner.preview(messy_df, action)
    twice = cleaner.preview(once, action)
    assert_frame_equal(once, twice)


# --------------------------------------------------------------------------- #
# Dispatch: run / preview / apply
# --------------------------------------------------------------------------- #


def test_preview_does_not_mutate_input(messy_df):
    snapshot = messy_df.clone()
    cleaner.preview(messy_df, make_action("trim_whitespace", ["customer_name"]))
    assert_frame_equal(messy_df, snapshot)


def test_apply_returns_audit_entry(messy_df, log_capture):
    action = make_action("drop_exact_duplicates")
    out, entry = cleaner.apply(messy_df, action)
    assert out.height == 21
    assert entry["action_id"] == action["action_id"]
    assert entry["operation"] == "drop_exact_duplicates"
    assert entry["rows_before"] == 24 and entry["rows_after"] == 21
    assert entry["human_decision"] == "approved" and entry["reversible"] is True
    assert "3 exact duplicate" in entry["summary"]
    assert any("audit | drop_exact_duplicates | rows 24 -> 21" in m for m in log_capture)


def test_apply_records_human_decision(messy_df):
    _, entry = cleaner.apply(messy_df, make_action("trim_whitespace", ["region"]), human_decision="edited")
    assert entry["human_decision"] == "edited"


def test_run_rejects_unknown_operation(messy_df):
    with pytest.raises(CleaningError, match="unknown operation"):
        cleaner.run(messy_df, make_action("delete_everything"))


def test_run_rejects_missing_column(messy_df):
    with pytest.raises(CleaningError, match="not found"):
        cleaner.run(messy_df, make_action("trim_whitespace", ["nope"]))


def test_run_rejects_empty_targets_where_required(messy_df):
    with pytest.raises(CleaningError, match="must not be empty"):
        cleaner.run(messy_df, make_action("trim_whitespace", []))


def test_dispatch_covers_every_contract_operation():
    from src.contracts import CLEANING_OPERATIONS

    assert set(cleaner._OPERATIONS) == CLEANING_OPERATIONS
