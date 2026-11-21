{#-
    Where the raw data comes from depends on the target:
      dev  -> the seed CSVs loaded by `dbt seed` (local DuckDB)
      prod -> the landing tables Azure Data Factory loads into Azure SQL (schema raw)
-#}
{% macro raw_table(name) %}
    {%- if target.name == 'prod' -%}
        {{ source('azure_raw', name) }}
    {%- else -%}
        {{ ref(name) }}
    {%- endif -%}
{% endmacro %}
