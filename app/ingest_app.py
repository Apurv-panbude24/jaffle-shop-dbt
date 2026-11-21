"""Ingest UI: upload any CSV / TSV / Parquet / Excel file, load it into DuckDB, see a summary and charts.

Run:  uv run streamlit run app/ingest_app.py
"""

import tempfile
from pathlib import Path

import streamlit as st

import azure_dest as az_dest
import ingest_core as core

st.set_page_config(page_title="Data ingest", page_icon="📥", layout="wide")


@st.cache_resource
def get_connection():
    return core.connect()


con = get_connection()

st.title("📥 Data ingest")
st.caption("Upload a file, load it into a local DuckDB database, and see what's in it. "
           "Data stays on this machine.")

# ---------------- sidebar: upload ----------------
with st.sidebar:
    st.header("1 · Upload")
    uploaded = st.file_uploader("CSV, TSV, Parquet or Excel", type=[s.lstrip(".") for s in core.SUPPORTED])
    default_name = core.clean_name(Path(uploaded.name).stem, "dataset") if uploaded else ""
    table_name = st.text_input("Table name", value=default_name, help="Re-using a name replaces that table.")
    to_azure = st.checkbox("Also send to Azure SQL", help="Uploads to ADLS and loads it through Data Factory.")
    go = st.button("Ingest", type="primary", disabled=uploaded is None or not table_name.strip())

    if go and uploaded is not None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / uploaded.name
            path.write_bytes(uploaded.getbuffer())
            try:
                result = core.ingest_file(con, path, table_name)
            except Exception as exc:  # surface parser errors in the UI instead of a stack trace
                st.error(f"Could not read this file: {exc}")
            else:
                st.session_state["selected"] = result.table
                st.session_state["last_result"] = result
                if to_azure:
                    st.session_state["send_azure"] = result.table
                st.success(f"Loaded {result.rows:,} rows into `{result.table}`")

    st.divider()
    st.header("Datasets")
    tables = core.list_tables(con)
    if tables.empty:
        st.info("Nothing ingested yet.")
        selected = None
    else:
        names = tables["table"].tolist()
        current = st.session_state.get("selected")
        selected = st.selectbox("Open", names, index=names.index(current) if current in names else 0)
        st.dataframe(tables, hide_index=True, width='stretch')
        if st.button("Delete this dataset"):
            core.drop_table(con, selected)
            st.session_state.pop("selected", None)
            st.rerun()

# ---------------- main: results ----------------
if selected is None:
    st.info("👈 Upload a file in the sidebar to get started.")
    st.stop()

last = st.session_state.get("last_result")
if last and last.table == selected and last.renamed:
    with st.expander(f"Column names tidied ({len(last.renamed)})"):
        st.table({"original": list(last.renamed), "cleaned": list(last.renamed.values())})

stats = core.headline(con, selected)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Rows", f"{stats['rows']:,}")
m2.metric("Columns", stats["columns"])
m3.metric("Empty cells", f"{stats['null_cells_pct']}%")
m4.metric("Duplicate rows", f"{stats['duplicate_rows']:,}")

# ---------------- Azure destination ----------------
st.subheader("Azure SQL")
pending = st.session_state.pop("send_azure", None)
if pending == selected or st.button("Send this dataset to Azure SQL"):
    try:
        cfg = az_dest.AzureConfig.from_env()
    except az_dest.AzureConfigError as exc:
        st.error(str(exc))
    else:
        with st.status("Sending to Azure…", expanded=True) as status:
            try:
                azure_result = az_dest.send_to_azure(con, selected, cfg, on_step=st.write)
            except Exception as exc:  # show the failing step instead of a stack trace
                status.update(label="Azure step failed", state="error")
                st.error(f"{type(exc).__name__}: {exc}")
            else:
                status.update(label="Done", state="complete")
                st.session_state.setdefault("azure_results", {})[selected] = azure_result

shown = st.session_state.get("azure_results", {}).get(selected)
if shown:
    a1, a2, a3 = st.columns(3)
    a1.metric("Rows in Azure SQL", f"{shown.rows_remote:,}", delta="matches local" if shown.matches else "MISMATCH",
              delta_color="normal" if shown.matches else "inverse")
    a2.metric("Time", f"{shown.seconds}s")
    a3.metric("Data Factory run", shown.run_id[:8])
    st.caption(f"Landed in ADLS at `{shown.adls_path}` and in Azure SQL as `uploads.{shown.table}`.")
else:
    st.caption("Not sent yet. Click the button, or tick the box in the sidebar before ingesting.")

tab_preview, tab_summary, tab_charts = st.tabs(["Preview", "Summary", "Charts"])

with tab_preview:
    st.dataframe(core.preview(con, selected), width='stretch', hide_index=True)
    st.caption("First 100 rows.")

with tab_summary:
    st.dataframe(core.summary(con, selected), width='stretch', hide_index=True)
    st.caption("Per-column type, range, mean, quartiles and share of empty values.")

with tab_charts:
    kinds = core.column_kinds(con, selected)
    # ID-style columns make poor histograms, so list them last.
    numeric = sorted((c for c, k in kinds.items() if k == "numeric"),
                     key=lambda c: c == "id" or c.endswith("_id"))
    temporal = [c for c, k in kinds.items() if k == "temporal"]
    category = [c for c, k in kinds.items() if k == "category"]

    if numeric:
        st.subheader("Distribution")
        col = st.selectbox("Numeric column", numeric, key="hist_col")
        st.bar_chart(core.histogram(con, selected, col), x="bin", y="count")

    if category:
        st.subheader("Most common values")
        col = st.selectbox("Column", category, key="top_col")
        st.bar_chart(core.top_values(con, selected, col), x="value", y="count")

    if temporal:
        st.subheader("Over time")
        date_col = st.selectbox("Date column", temporal, key="ts_date")
        measure = st.selectbox("Measure", ["Row count"] + numeric, key="ts_measure")
        series = core.time_series(con, selected, date_col, None if measure == "Row count" else measure)
        st.line_chart(series, x="period", y="value")

    if not (numeric or category or temporal):
        st.info("No chartable columns found.")
