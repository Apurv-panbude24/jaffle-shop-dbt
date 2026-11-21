"""Azure destination for the ingest UI.

Flow for one dataset that is already loaded locally in DuckDB:
  DuckDB table -> CSV -> ADLS (raw/uploads/<table>.csv)
               -> CREATE TABLE uploads.<table> in Azure SQL (types mapped from DuckDB)
               -> Data Factory pipeline `pl_ingest_upload` loads the CSV into that table
               -> row count in Azure SQL is compared with the local row count

Authentication is keyless for Azure resources (DefaultAzureCredential, i.e. your `az login`);
only Azure SQL uses a password, read from the environment or a private file.
Config comes from environment variables, see `AzureConfig.from_env`.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import duckdb

from ingest_core import SCHEMA as LOCAL_SCHEMA
from ingest_core import quote as q_duck

AZURE_SCHEMA = "uploads"
PIPELINE = "pl_ingest_upload"
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
DEFAULT_PASSWORD_FILE = Path.home() / ".config" / "jaffle-shop" / "sql_password"


class AzureConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class AzureConfig:
    subscription_id: str
    resource_group: str
    storage_account: str
    factory: str
    sql_server: str  # fully qualified, e.g. sql-xyz.database.windows.net
    sql_password: str
    sql_user: str = "jaffleadmin"
    database: str = "jaffle_shop"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AzureConfig":
        env = dict(os.environ if env is None else env)
        password = env.get("AZURE_SQL_PASSWORD", "")
        pw_file = Path(env.get("AZURE_SQL_PASSWORD_FILE", DEFAULT_PASSWORD_FILE))
        if not password and pw_file.exists():
            password = pw_file.read_text().strip()
        values = {
            "AZURE_SUBSCRIPTION_ID": env.get("AZURE_SUBSCRIPTION_ID", ""),
            "AZURE_RESOURCE_GROUP": env.get("AZURE_RESOURCE_GROUP", ""),
            "AZURE_STORAGE_ACCOUNT": env.get("AZURE_STORAGE_ACCOUNT", ""),
            "AZURE_DATA_FACTORY": env.get("AZURE_DATA_FACTORY", ""),
            "AZURE_SQL_SERVER": env.get("AZURE_SQL_SERVER", ""),
            "AZURE_SQL_PASSWORD": password,
        }
        missing = [k for k, v in values.items() if not v]
        if missing:
            raise AzureConfigError("Missing: " + ", ".join(missing) + ". See infra/azure/env.sh.")
        return cls(
            subscription_id=values["AZURE_SUBSCRIPTION_ID"],
            resource_group=values["AZURE_RESOURCE_GROUP"],
            storage_account=values["AZURE_STORAGE_ACCOUNT"],
            factory=values["AZURE_DATA_FACTORY"],
            sql_server=values["AZURE_SQL_SERVER"],
            sql_password=values["AZURE_SQL_PASSWORD"],
        )


# ---------------------------------------------------------------- pure helpers (unit-tested)

_EXACT_TYPES = {
    "BOOLEAN": "bit",
    "TINYINT": "smallint",
    "SMALLINT": "smallint",
    "INTEGER": "int",
    "BIGINT": "bigint",
    "UTINYINT": "smallint",
    "USMALLINT": "int",
    "UINTEGER": "bigint",
    "UBIGINT": "decimal(20,0)",
    "HUGEINT": "decimal(38,0)",
    "UHUGEINT": "decimal(38,0)",
    "FLOAT": "real",
    "DOUBLE": "float",
    "DATE": "date",
    "TIME": "time",
    "TIMESTAMP": "datetime2",
    "TIMESTAMP WITH TIME ZONE": "datetimeoffset",
}


def sqlserver_type(duck_type: str) -> str:
    """Map a DuckDB column type to a SQL Server type. Unknown or nested types fall back to text."""
    t = duck_type.upper().strip()
    if t in _EXACT_TYPES:
        return _EXACT_TYPES[t]
    m = re.fullmatch(r"DECIMAL\((\d+),\s*(\d+)\)", t)
    if m:
        return f"decimal({min(int(m.group(1)), 38)},{min(int(m.group(2)), 38)})"
    return "nvarchar(max)"


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Unsafe identifier: {name!r}")
    return f"[{name}]"


def create_table_sql(table: str, columns: list[tuple[str, str]]) -> str:
    """Idempotent DDL: schema if missing, then the table dropped and recreated with mapped types."""
    cols = ",\n    ".join(f"{_ident(n)} {sqlserver_type(t)} NULL" for n, t in columns)
    full = f"{_ident(AZURE_SCHEMA)}.{_ident(table)}"
    return (
        f"IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = '{AZURE_SCHEMA}') EXEC('CREATE SCHEMA {AZURE_SCHEMA}');\n"
        f"IF OBJECT_ID('{AZURE_SCHEMA}.{table}', 'U') IS NOT NULL DROP TABLE {full};\n"
        f"CREATE TABLE {full} (\n    {cols}\n);"
    )


def local_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[tuple[str, str]]:
    rows = con.execute(f"describe {LOCAL_SCHEMA}.{q_duck(table)}").fetchall()
    return [(r[0], r[1]) for r in rows]


def export_csv(con: duckdb.DuckDBPyConnection, table: str, path: Path) -> int:
    """Write the local table to CSV (header, RFC-4180 quoting). Returns the row count."""
    con.execute(f"copy (select * from {LOCAL_SCHEMA}.{q_duck(table)}) to ? (format csv, header true)", [str(path)])
    return con.execute(f"select count(*) from {LOCAL_SCHEMA}.{q_duck(table)}").fetchone()[0]


# ---------------------------------------------------------------- Azure calls (integration-tested live)

def _credential():
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def upload_csv(cfg: AzureConfig, path: Path, table: str) -> str:
    from azure.storage.filedatalake import DataLakeServiceClient
    service = DataLakeServiceClient(f"https://{cfg.storage_account}.dfs.core.windows.net", credential=_credential())
    file = service.get_file_system_client("raw").get_file_client(f"uploads/{table}.csv")
    with open(path, "rb") as fh:
        file.upload_data(fh, overwrite=True)
    return f"raw/uploads/{table}.csv"


def _sql(cfg: AzureConfig):
    import pymssql
    return pymssql.connect(server=cfg.sql_server, user=cfg.sql_user, password=cfg.sql_password,
                           database=cfg.database, login_timeout=120, autocommit=True)


def create_remote_table(cfg: AzureConfig, table: str, columns: list[tuple[str, str]]) -> None:
    ddl = create_table_sql(table, columns)
    conn = _sql(cfg)
    try:
        cur = conn.cursor()
        for stmt in [s for s in ddl.split(";\n") if s.strip()]:
            cur.execute(stmt.rstrip(";"))
    finally:
        conn.close()


def remote_row_count(cfg: AzureConfig, table: str) -> int:
    conn = _sql(cfg)
    try:
        cur = conn.cursor()
        cur.execute(f"select count(*) from {_ident(AZURE_SCHEMA)}.{_ident(table)}")
        return cur.fetchone()[0]
    finally:
        conn.close()


def run_pipeline(cfg: AzureConfig, table: str, timeout_s: int = 600,
                 on_status: Callable[[str], None] | None = None) -> str:
    """Start pl_ingest_upload for `table` and wait. Returns the run id; raises on failure."""
    from azure.mgmt.datafactory import DataFactoryManagementClient
    client = DataFactoryManagementClient(_credential(), cfg.subscription_id)
    run = client.pipelines.create_run(cfg.resource_group, cfg.factory, PIPELINE, parameters={"tableName": table})
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        status = client.pipeline_runs.get(cfg.resource_group, cfg.factory, run.run_id)
        if status.status != last and on_status:
            on_status(status.status)
        last = status.status
        if status.status == "Succeeded":
            return run.run_id
        if status.status in ("Failed", "Cancelled"):
            raise RuntimeError(f"Pipeline {status.status}: {status.message}")
        time.sleep(4)
    raise TimeoutError(f"Pipeline still running after {timeout_s}s (run {run.run_id})")


@dataclass
class AzureResult:
    table: str
    rows_local: int
    rows_remote: int
    run_id: str
    adls_path: str
    seconds: float

    @property
    def matches(self) -> bool:
        return self.rows_local == self.rows_remote


def send_to_azure(con: duckdb.DuckDBPyConnection, table: str, cfg: AzureConfig,
                  on_step: Callable[[str], None] | None = None) -> AzureResult:
    """Run the whole flow for one local table and verify the row count at the end."""
    step = on_step or (lambda _msg: None)
    started = time.time()
    columns = local_columns(con, table)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{table}.csv"
        step("Exporting cleaned data to CSV")
        rows_local = export_csv(con, table, path)
        step("Uploading to ADLS Gen2")
        adls_path = upload_csv(cfg, path, table)
    step("Creating the table in Azure SQL")
    create_remote_table(cfg, table, columns)
    step("Running the Data Factory pipeline")
    run_id = run_pipeline(cfg, table, on_status=lambda s: step(f"Data Factory: {s}"))
    step("Checking the row count")
    rows_remote = remote_row_count(cfg, table)
    return AzureResult(table, rows_local, rows_remote, run_id, adls_path, round(time.time() - started, 1))
