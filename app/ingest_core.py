"""Core logic for the ingest UI: load any tabular file into DuckDB, then summarise it.

Kept free of Streamlit so it can be unit-tested. Identifiers coming from user files are
always sanitised and quoted; file contents are never interpolated into SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

SCHEMA = "uploads"
DEFAULT_DB = Path("data/ingest.duckdb")
SUPPORTED = (".csv", ".tsv", ".txt", ".parquet", ".xlsx")

_NUMERIC_PREFIXES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
                     "UINTEGER", "UBIGINT", "FLOAT", "DOUBLE", "DECIMAL", "REAL")
_TEMPORAL_PREFIXES = ("DATE", "TIMESTAMP")


def clean_name(name: object, fallback: str) -> str:
    """snake_case identifier: 'Order Date (UTC)' -> 'order_date_utc', 'userID' -> 'user_id'."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name).strip())
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower()
    if not s:
        return fallback
    if s[0].isdigit():
        s = f"c_{s}"
    return s


def clean_columns(columns: list[object]) -> list[str]:
    """Clean every name and make them unique (a, a_2, a_3...)."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, col in enumerate(columns, start=1):
        base = clean_name(col, f"column_{i}")
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def connect(db_path: Path | str = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(f"create schema if not exists {SCHEMA}")
    return con


def _qualified(table: str) -> str:
    return f"{SCHEMA}.{quote(table)}"


@dataclass
class IngestResult:
    table: str
    rows: int
    columns: int
    renamed: dict[str, str]  # original -> cleaned, only where they differ


def ingest_file(con: duckdb.DuckDBPyConnection, path: Path | str, table: str | None = None) -> IngestResult:
    """Load a file into uploads.<table> (replacing it), with cleaned column names."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"Unsupported file type '{suffix}'. Supported: {', '.join(SUPPORTED)}")

    table = clean_name(table or path.stem, "dataset")
    target = _qualified(table)

    if suffix in (".csv", ".tsv", ".txt"):
        con.execute(
            f"create or replace table {target} as "
            "select * from read_csv(?, auto_detect=true, sample_size=-1, header=true)",
            [str(path)],
        )
    elif suffix == ".parquet":
        con.execute(f"create or replace table {target} as select * from read_parquet(?)", [str(path)])
    else:  # .xlsx
        frame = pd.read_excel(path)
        con.register("_upload_frame", frame)
        try:
            con.execute(f"create or replace table {target} as select * from _upload_frame")
        finally:
            con.unregister("_upload_frame")

    original = [r[0] for r in con.execute(f"select column_name from information_schema.columns "
                                          f"where table_schema = '{SCHEMA}' and table_name = ? "
                                          "order by ordinal_position", [table]).fetchall()]
    cleaned = clean_columns(original)
    renamed = {}
    # Two-step rename so swapping/colliding names can never clash mid-way.
    temp_names = [f"__tmp_{i}" for i in range(len(original))]
    for old, tmp in zip(original, temp_names):
        con.execute(f"alter table {target} rename column {quote(old)} to {quote(tmp)}")
    for tmp, old, new in zip(temp_names, original, cleaned):
        con.execute(f"alter table {target} rename column {quote(tmp)} to {quote(new)}")
        if old != new:
            renamed[old] = new

    rows = con.execute(f"select count(*) from {target}").fetchone()[0]
    return IngestResult(table=table, rows=rows, columns=len(original), renamed=renamed)


def list_tables(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    names = [r[0] for r in con.execute(
        "select table_name from information_schema.tables where table_schema = ? order by 1", [SCHEMA]).fetchall()]
    rows = [(n, con.execute(f"select count(*) from {_qualified(n)}").fetchone()[0]) for n in names]
    return pd.DataFrame(rows, columns=["table", "rows"])


def drop_table(con: duckdb.DuckDBPyConnection, table: str) -> None:
    con.execute(f"drop table if exists {_qualified(clean_name(table, 'dataset'))}")


def preview(con: duckdb.DuckDBPyConnection, table: str, limit: int = 100) -> pd.DataFrame:
    return con.execute(f"select * from {_qualified(table)} limit {int(limit)}").df()


def headline(con: duckdb.DuckDBPyConnection, table: str) -> dict:
    """Row/column counts, share of empty cells, and exact duplicate rows."""
    t = _qualified(table)
    rows = con.execute(f"select count(*) from {t}").fetchone()[0]
    cols = [r[0] for r in con.execute(f"describe {t}").fetchall()]
    if rows == 0:
        return {"rows": 0, "columns": len(cols), "null_cells_pct": 0.0, "duplicate_rows": 0}
    null_sum = " + ".join(f"count(*) - count({quote(c)})" for c in cols)
    nulls = con.execute(f"select {null_sum} from {t}").fetchone()[0]
    distinct_rows = con.execute(f"select count(*) from (select distinct * from {t})").fetchone()[0]
    return {
        "rows": rows,
        "columns": len(cols),
        "null_cells_pct": round(100 * nulls / (rows * len(cols)), 2),
        "duplicate_rows": rows - distinct_rows,
    }


def summary(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame:
    """Per-column summary (type, min/max, mean, quartiles, null %) from DuckDB's SUMMARIZE."""
    return con.execute(f"summarize {_qualified(table)}").df()


def column_kinds(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    """Map each column to 'numeric', 'temporal' or 'category'."""
    kinds = {}
    for name, dtype, *_ in con.execute(f"describe {_qualified(table)}").fetchall():
        upper = dtype.upper()
        if upper.startswith(_NUMERIC_PREFIXES):
            kinds[name] = "numeric"
        elif upper.startswith(_TEMPORAL_PREFIXES):
            kinds[name] = "temporal"
        else:
            kinds[name] = "category"
    return kinds


def histogram(con: duckdb.DuckDBPyConnection, table: str, column: str, bins: int = 20) -> pd.DataFrame:
    values = con.execute(f"select {quote(column)} from {_qualified(table)} where {quote(column)} is not null").df()
    series = pd.to_numeric(values[column], errors="coerce").dropna()
    if series.empty:
        return pd.DataFrame(columns=["bin", "count"])
    counts, edges = np.histogram(series, bins=min(bins, max(1, series.nunique())))
    labels = [f"{edges[i]:.4g} – {edges[i + 1]:.4g}" for i in range(len(counts))]
    return pd.DataFrame({"bin": labels, "count": counts})


def top_values(con: duckdb.DuckDBPyConnection, table: str, column: str, n: int = 10) -> pd.DataFrame:
    return con.execute(
        f"select cast({quote(column)} as varchar) as value, count(*) as count "
        f"from {_qualified(table)} group by 1 order by 2 desc, 1 limit {int(n)}"
    ).df()


def time_series(con: duckdb.DuckDBPyConnection, table: str, date_column: str,
                value_column: str | None = None) -> pd.DataFrame:
    """Rows (or the sum of value_column) per day/week/month, granularity picked from the date span."""
    t, d = _qualified(table), quote(date_column)
    lo, hi = con.execute(f"select min({d}), max({d}) from {t}").fetchone()
    if lo is None:
        return pd.DataFrame(columns=["period", "value"])
    span_days = (pd.Timestamp(hi) - pd.Timestamp(lo)).days
    grain = "month" if span_days > 730 else "week" if span_days > 90 else "day"
    measure = f"sum({quote(value_column)})" if value_column else "count(*)"
    return con.execute(
        f"select date_trunc('{grain}', {d})::date as period, {measure} as value "
        f"from {t} where {d} is not null group by 1 order by 1"
    ).df()
