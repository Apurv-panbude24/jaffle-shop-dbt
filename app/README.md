# Ingest UI

A small Streamlit app: upload a file, load it into a local DuckDB database, and see a
summary and charts. Nothing leaves your machine.

```bash
uv run streamlit run app/ingest_app.py     # opens http://localhost:8501
uv run pytest tests_app                    # unit tests for the core logic
```

**What it does**
- Accepts CSV, TSV, Parquet and Excel files.
- Detects column types automatically (DuckDB `read_csv` with full-file sampling).
- Tidies column names to `snake_case` (`Order Date (UTC)` -> `order_date_utc`) and makes
  them unique; the UI lists what it renamed.
- Loads into `data/ingest.duckdb`, schema `uploads`. Re-using a table name replaces it.
- Shows row/column counts, share of empty cells, exact duplicate rows, a per-column
  summary (type, min/max, mean, quartiles, null %) and charts (distribution, most common
  values, rows or totals over time).

**Design notes**
- `ingest_core.py` holds all logic and has no Streamlit imports, so it is unit-tested directly.
- Identifiers from user files are always sanitised and quoted; file contents are never
  interpolated into SQL (a hostile table name like `x"; drop table y; --` becomes `x_drop_table_y`).

## Azure SQL destination

Tick **Also send to Azure SQL** before ingesting, or click **Send this dataset to Azure SQL**
for any loaded dataset. The app then:

1. exports the cleaned table to CSV,
2. uploads it to ADLS Gen2 (`raw/uploads/<table>.csv`),
3. creates `uploads.<table>` in Azure SQL with column types mapped from DuckDB
   (`INTEGER`->`int`, `DATE`->`date`, `BOOLEAN`->`bit`, `DECIMAL(p,s)`->`decimal(p,s)`, text->`nvarchar(max)`),
4. runs the generic Data Factory pipeline `pl_ingest_upload` (truncate + load) and waits for it,
5. compares the row count in Azure SQL with the local one and shows the result.

Setup (after `infra/azure/deploy.sh`, with `UPLOADER_OBJECT_ID` set so your login may upload):

```bash
az login
eval "$(./infra/azure/env.sh)"     # exports resource names; the SQL password is read from
                                   # AZURE_SQL_PASSWORD or ~/.config/jaffle-shop/sql_password
uv run streamlit run app/ingest_app.py
```

Azure resources are accessed keylessly with your `az login` (Entra ID); only Azure SQL uses a password.

**Not covered yet:** generated dbt models per uploaded dataset, and a cleaned-CSV download.
