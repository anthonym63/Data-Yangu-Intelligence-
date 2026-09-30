"""Analytics: correct results, and a query surface that cannot write or read files."""

from __future__ import annotations

import polars as pl
import pytest

from src.backend import analytics, cleaner
from src.backend.analytics import AnalyticsError
from tests.conftest import make_action


def test_run_query_select(messy_df):
    out = analytics.run_query(messy_df, "SELECT region, count(*) AS n FROM dataset GROUP BY region ORDER BY n DESC")
    assert isinstance(out, pl.DataFrame)
    assert out.columns == ["region", "n"]
    assert out.get_column("n").sum() == 24


def test_run_query_with_cte_and_trailing_semicolon(messy_df):
    out = analytics.run_query(messy_df, "WITH t AS (SELECT amount_kes FROM dataset) SELECT sum(amount_kes) AS s FROM t;")
    assert out.item() == pytest.approx(messy_df.get_column("amount_kes").sum())


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE dataset",
        "DELETE FROM dataset",
        "INSERT INTO dataset VALUES (1)",
        "SELECT 1; SELECT 2",
        "SELECT * FROM dataset; DROP TABLE dataset",
        "CREATE TABLE x AS SELECT * FROM dataset",
        "SELECT * FROM dataset WHERE 1=1 UNION ALL SELECT * FROM (INSERT INTO dataset VALUES (1))",
        "PRAGMA database_list",
        "SET enable_external_access = true",
        "",
        "   ;  ",
    ],
)
def test_run_query_rejects_non_readonly(messy_df, sql):
    with pytest.raises(AnalyticsError):
        analytics.run_query(messy_df, sql)


def test_run_query_cannot_touch_filesystem(messy_df, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    with pytest.raises(AnalyticsError):
        analytics.run_query(messy_df, f"SELECT * FROM read_csv_auto('{secret.as_posix()}')")


def test_run_query_surfaces_sql_errors(messy_df):
    with pytest.raises(AnalyticsError):
        analytics.run_query(messy_df, "SELECT nonexistent_column FROM dataset")


# --------------------------------------------------------------------------- #
# Built queries
# --------------------------------------------------------------------------- #


def test_aggregate_sum(messy_df):
    out = analytics.aggregate(messy_df, "region", "amount_kes", "sum")
    assert out.columns == ["region", "value"]
    assert out.height == messy_df.get_column("region").n_unique()
    values = out.get_column("value")
    assert values.drop_nulls().is_sorted(descending=True)
    assert values.to_list()[-1] is None  # "Thika": its only amount is null -> NULL sum, ordered last
    assert values.sum() == pytest.approx(messy_df.get_column("amount_kes").sum())


def test_registration_is_stable_across_repeated_queries(messy_df):
    """Regression: registering the Polars frame directly faulted on this stack
    (spec 7.1). Ten back-to-back queries on the fixture must all succeed."""
    for _ in range(10):
        out = analytics.aggregate(messy_df, "region", "amount_kes", "sum")
        assert out.height == messy_df.get_column("region").n_unique()


def test_aggregate_count_on_text(messy_df):
    out = analytics.aggregate(messy_df, "region", "customer_name", "count")
    assert out.get_column("value").sum() == 24


def test_aggregate_top_n(messy_df):
    assert analytics.aggregate(messy_df, "customer_name", "amount_kes", top_n=3).height == 3


def test_aggregate_rejects_bad_inputs(messy_df):
    with pytest.raises(AnalyticsError, match="not found"):
        analytics.aggregate(messy_df, "nope", "amount_kes")
    with pytest.raises(AnalyticsError, match="agg"):
        analytics.aggregate(messy_df, "region", "amount_kes", "explode")
    with pytest.raises(AnalyticsError, match="numeric"):
        analytics.aggregate(messy_df, "region", "customer_name", "sum")


def test_quoted_identifiers_survive_odd_names():
    df = pl.DataFrame({'we"ird col': ["a", "a", "b"], "amount (kes)": [1.0, 2.0, 3.0]})
    out = analytics.aggregate(df, 'we"ird col', "amount (kes)")
    assert out.height == 2 and out.get_column("value").to_list() == [3.0, 3.0]


def test_value_counts(messy_df):
    out = analytics.value_counts(messy_df, "region", top_n=3)
    assert out.columns == ["value", "count"] and out.height == 3
    assert out.get_column("count").is_sorted(descending=True)


def test_numeric_summary(messy_df):
    out = analytics.numeric_summary(messy_df, "amount_kes")
    row = out.row(0, named=True)
    assert row["count"] == 23 and row["nulls"] == 1
    assert row["min"] == 300.0 and row["max"] == 890000.0
    assert row["q1"] <= row["median"] <= row["q3"]
    with pytest.raises(AnalyticsError):
        analytics.numeric_summary(messy_df, "email")


def test_timeseries_over_standardized_string_dates(messy_df):
    clean = cleaner.preview(messy_df, make_action("standardize_dates", ["order_date"]))
    out = analytics.timeseries(clean, "order_date", "amount_kes", grain="month")
    assert out.columns == ["period", "value"]
    assert out.height == 2  # Jan and Feb 2024; "not a date" and null are excluded
    assert out.get_column("value").sum() == pytest.approx(
        clean.filter(pl.col("order_date").str.starts_with("2024")).get_column("amount_kes").sum()
    )


def test_timeseries_rejects_bad_grain(messy_df):
    with pytest.raises(AnalyticsError, match="grain"):
        analytics.timeseries(messy_df, "order_date", "amount_kes", grain="fortnight")
