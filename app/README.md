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

**Not covered yet:** an Azure SQL destination (needs a generic Data Factory pipeline), and
generated dbt models per dataset.
