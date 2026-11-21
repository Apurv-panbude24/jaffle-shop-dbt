# Azure leg

```
GitHub raw CSVs -> Data Factory (pl_ingest_raw) -> ADLS Gen2 (raw container)
                -> Azure SQL Database (schema raw) -> dbt (target prod, schema analytics)
```

| Piece | Resource | Why |
|---|---|---|
| Landing zone | ADLS Gen2 storage account, container `raw` | Cheap, durable copy of the source files |
| Ingestion | Data Factory pipeline `pl_ingest_raw` (ForEach table: CSV to ADLS, then ADLS to SQL) | Config-driven by the `tables` parameter; daily trigger `tr_daily` |
| Generic uploads | Pipeline `pl_ingest_upload` (one table per run, parameter `tableName`), used by the ingest UI | Any CSV the UI sends lands in `uploads.<table>` |
| Warehouse | Azure SQL Database, free serverless offer, auto-pause | Zero cost at this size; dbt runs on top |
| Access | Data Factory uses its managed identity for storage; no storage keys | Nothing to leak or rotate |
| Cost guard | Monthly budget with email alerts, `teardown.sh` | Nothing runs unnoticed |

## Deploy

```bash
az login
export SQL_ADMIN_PASSWORD='...'      # random, 12+ chars; never commit it
export ALERT_EMAIL='you@example.com'
./infra/azure/deploy.sh rg-jaffle-shop germanywestcentral
```

Create the landing tables once (`scripts/sql/create_raw_tables.sql`), then run the pipeline:

```bash
az datafactory pipeline create-run -g rg-jaffle-shop --factory-name <adf-name> --name pl_ingest_raw
az datafactory trigger start -g rg-jaffle-shop --factory-name <adf-name> -n tr_daily   # optional: daily schedule
```

## Run dbt against Azure

Prerequisites on macOS: Microsoft ODBC Driver 18 (`microsoft/mssql-release` tap) and `uv`.

```bash
export AZURE_SQL_SERVER=<server>.database.windows.net
export AZURE_SQL_PASSWORD='...'
# Microsoft's driver does not find Homebrew's OpenSSL by itself on Apple Silicon:
export DYLD_LIBRARY_PATH=/opt/homebrew/opt/openssl@3/lib
uv run dbt build --target prod
```

`dev` (local DuckDB, seeds) and `prod` (Azure SQL, landing tables) run the same models.
`macros/raw_table.sql` switches the source by target, and a few expressions are written to
be valid on both engines (for example `dbt.dateadd`, `case when` instead of boolean
expressions, `cast` before division).

The serverless database pauses when idle. The first connection after a pause fails with
"database is not currently available"; retry after about a minute.

## Tear down

```bash
./infra/azure/teardown.sh rg-jaffle-shop
```
