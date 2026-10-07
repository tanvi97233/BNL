from __future__ import annotations

from datetime import date, timedelta

import streamlit as st
from dotenv import load_dotenv

from bnl_tool.config import ROOT, Settings, last_working_day
from bnl_tool.pipeline import run_pipeline

load_dotenv(ROOT / ".env")
st.set_page_config(page_title="BNL SOP Automation", layout="wide")
st.title("BNL SOP Literature Automation")
default_end = last_working_day(date.today())
start = st.date_input("Start date", default_end - timedelta(days=14))
end = st.date_input("End date", default_end)
dry_run = st.checkbox("Dry run (2 strings, 1 page each)")
max_pages = st.number_input("Maximum Google pages per search string", min_value=1, max_value=10, value=5)

if st.button("Run", type="primary"):
    if start > end:
        st.error("Start date must not be after end date.")
    else:
        log_box = st.empty(); messages: list[str] = []
        def progress(message: str) -> None:
            messages.append(message); log_box.code("\n".join(messages[-30:]))
        try:
            progress(f"Confirmed date range: {start} to {end}")
            result = run_pipeline(Settings.from_env(), start, end, max_pages=int(max_pages), dry_run=dry_run, resume=True, progress=progress)
            st.success("Complete" if result.qc.passed else "Complete — QC failed; report is marked DRAFT")
            for label, path, mime in [("Download DOCX", result.report_path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), ("Download audit log", result.audit_path, "text/csv"), ("Download QC report", result.qc_path, "text/plain")]:
                st.download_button(label, path.read_bytes(), file_name=path.name, mime=mime)
        except Exception as exc:
            st.exception(exc)
