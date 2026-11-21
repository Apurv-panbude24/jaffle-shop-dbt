import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import azure_dest as az  # noqa: E402
import ingest_core as core  # noqa: E402


@pytest.mark.parametrize("duck, sql", [
    ("BOOLEAN", "bit"),
    ("INTEGER", "int"),
    ("BIGINT", "bigint"),
    ("DOUBLE", "float"),
    ("DATE", "date"),
    ("TIMESTAMP", "datetime2"),
    ("TIMESTAMP WITH TIME ZONE", "datetimeoffset"),
    ("DECIMAL(18,3)", "decimal(18,3)"),
    ("VARCHAR", "nvarchar(max)"),
    ("INTEGER[]", "nvarchar(max)"),  # nested types fall back to text
])
def test_type_mapping(duck, sql):
    assert az.sqlserver_type(duck) == sql


def test_create_table_sql_is_idempotent_and_typed():
    ddl = az.create_table_sql("sales", [("order_id", "INTEGER"), ("amount", "DECIMAL(10,2)"), ("note", "VARCHAR")])
    assert "DROP TABLE [uploads].[sales]" in ddl
    assert "[order_id] int NULL" in ddl
    assert "[amount] decimal(10,2) NULL" in ddl
    assert "[note] nvarchar(max) NULL" in ddl


@pytest.mark.parametrize("bad", ["x]; drop table y;--", "Has Space", "1abc", "", "a-b"])
def test_unsafe_identifiers_are_refused(bad):
    with pytest.raises(ValueError):
        az.create_table_sql(bad, [("a", "INTEGER")])
    with pytest.raises(ValueError):
        az.create_table_sql("ok", [(bad, "INTEGER")])


def test_every_cleaned_name_passes_the_identifier_check():
    # Whatever the UI produces from hostile input must be accepted by the Azure DDL builder.
    for raw in ['Order ID', "x\"; drop table y; --", "2nd col", "###", "userID"]:
        name = core.clean_name(raw, "column_1")
        az.create_table_sql(name, [(name, "INTEGER")])


def test_export_csv_roundtrip(tmp_path):
    con = core.connect(tmp_path / "t.duckdb")
    src = tmp_path / "in.csv"
    src.write_text('Name,Note,Amount\nAnn,"hello, world",1.5\nBob,,2\n')
    core.ingest_file(con, src, "people")
    out = tmp_path / "out.csv"
    assert az.export_csv(con, "people", out) == 2
    text = out.read_text().splitlines()
    assert text[0] == "name,note,amount"
    assert '"hello, world"' in text[1]
    assert az.local_columns(con, "people") == [("name", "VARCHAR"), ("note", "VARCHAR"), ("amount", "DOUBLE")]


def test_config_reports_what_is_missing(tmp_path):
    with pytest.raises(az.AzureConfigError) as e:
        az.AzureConfig.from_env({"AZURE_SQL_PASSWORD_FILE": str(tmp_path / "none")})
    assert "AZURE_SQL_SERVER" in str(e.value) and "AZURE_SQL_PASSWORD" in str(e.value)


def test_config_reads_password_from_file(tmp_path):
    pw = tmp_path / "pw"
    pw.write_text("s3cret\n")
    cfg = az.AzureConfig.from_env({
        "AZURE_SUBSCRIPTION_ID": "s", "AZURE_RESOURCE_GROUP": "rg", "AZURE_STORAGE_ACCOUNT": "st",
        "AZURE_DATA_FACTORY": "adf", "AZURE_SQL_SERVER": "srv", "AZURE_SQL_PASSWORD_FILE": str(pw),
    })
    assert cfg.sql_password == "s3cret"


def test_remote_row_mismatch_is_flagged():
    r = az.AzureResult("t", 10, 9, "run", "raw/uploads/t.csv", 1.0)
    assert not r.matches
