import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import ingest_core as core  # noqa: E402

MESSY_CSV = (
    "Order ID,Customer Name,Order Date (UTC),Amount $,userID,Order ID\n"
    "1,Ann,2024-01-01,10.5,7,x\n"
    "2,Bob,2024-01-02,20.0,8,y\n"
    "2,Bob,2024-01-02,20.0,8,y\n"
    "3,,2024-02-01,,9,z\n"
)


@pytest.fixture
def con(tmp_path):
    c = core.connect(tmp_path / "t.duckdb")
    yield c
    c.close()


@pytest.fixture
def messy(tmp_path):
    p = tmp_path / "Sales Data 2024.csv"
    p.write_text(MESSY_CSV)
    return p


def test_clean_name_cases():
    assert core.clean_name("Order Date (UTC)", "x") == "order_date_utc"
    assert core.clean_name("userID", "x") == "user_id"
    assert core.clean_name("2nd col", "x") == "c_2nd_col"
    assert core.clean_name("###", "fallback") == "fallback"


def test_clean_columns_are_unique():
    assert core.clean_columns(["A b", "a_b", "A-B"]) == ["a_b", "a_b_2", "a_b_3"]


def test_ingest_messy_csv(con, messy):
    result = core.ingest_file(con, messy)
    assert result.table == "sales_data_2024"
    assert result.rows == 4
    assert result.renamed["Order ID"] == "order_id"
    cols = list(core.preview(con, result.table).columns)
    # DuckDB itself de-duplicates the repeated header ("Order ID_1") before we clean it.
    assert cols == ["order_id", "customer_name", "order_date_utc", "amount", "user_id", "order_id_1"]


def test_types_are_inferred(con, messy):
    core.ingest_file(con, messy)
    kinds = core.column_kinds(con, "sales_data_2024")
    assert kinds["order_date_utc"] == "temporal"
    assert kinds["amount"] == "numeric"
    assert kinds["customer_name"] == "category"


def test_headline_counts_nulls_and_duplicates(con, messy):
    core.ingest_file(con, messy)
    h = core.headline(con, "sales_data_2024")
    assert h["rows"] == 4 and h["columns"] == 6
    assert h["duplicate_rows"] == 1
    assert h["null_cells_pct"] == round(100 * 2 / 24, 2)  # customer_name and amount are empty once


def test_reingest_replaces_table(con, messy, tmp_path):
    core.ingest_file(con, messy, "sales")
    smaller = tmp_path / "s.csv"
    smaller.write_text("a,b\n1,2\n")
    core.ingest_file(con, smaller, "sales")
    assert core.list_tables(con).to_dict("records") == [{"table": "sales", "rows": 1}]


def test_charts_data(con, messy):
    core.ingest_file(con, messy)
    t = "sales_data_2024"
    assert core.histogram(con, t, "amount")["count"].sum() == 3  # nulls excluded
    top = core.top_values(con, t, "customer_name")
    assert top.iloc[0]["value"] == "Bob" and top.iloc[0]["count"] == 2
    series = core.time_series(con, t, "order_date_utc", "amount")
    assert series["value"].sum() == pytest.approx(50.5)
    assert len(core.time_series(con, t, "order_date_utc")) == len(series)


def test_summary_has_a_row_per_column(con, messy):
    core.ingest_file(con, messy)
    s = core.summary(con, "sales_data_2024")
    assert len(s) == 6 and "null_percentage" in s.columns


def test_unsupported_extension_rejected(con, tmp_path):
    p = tmp_path / "x.exe"
    p.write_text("nope")
    with pytest.raises(ValueError):
        core.ingest_file(con, p)


def test_hostile_table_name_is_neutralised(con, messy):
    result = core.ingest_file(con, messy, 'x"; drop table y; --')
    assert result.table == "x_drop_table_y"
    assert core.list_tables(con)["table"].tolist() == ["x_drop_table_y"]


def test_parquet_roundtrip(con, tmp_path):
    p = tmp_path / "d.parquet"
    con.execute("copy (select 1 as 'My Col', 'a' as Name) to ? (format parquet)", [str(p)])
    r = core.ingest_file(con, p)
    assert list(core.preview(con, r.table).columns) == ["my_col", "name"]


def test_drop_table(con, messy):
    core.ingest_file(con, messy, "sales")
    core.drop_table(con, "sales")
    assert core.list_tables(con).empty
