from __future__ import annotations

"""Single-workflow Streamlit entry point for BNL Research Automation."""

from datetime import date, timedelta
from pathlib import Path
import time

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

try:  # Community Cloud runs from the repository root.
    from bnl_research_automation.core import NEWSLETTER_COLUMNS, exports, newsletter_rows, valid_search_strings
    from bnl_research_automation.pipeline import FUNNEL_KEYS, new_run_state, run_newsletter_search
    from bnl_research_automation.providers import search_europe_pmc
    from bnl_research_automation.screening import ai_provider
except ModuleNotFoundError:  # Local: python -m streamlit run app.py
    from core import NEWSLETTER_COLUMNS, exports, newsletter_rows, valid_search_strings
    from pipeline import FUNNEL_KEYS, new_run_state, run_newsletter_search
    from providers import search_europe_pmc
    from screening import ai_provider


ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
st.set_page_config(page_title="BNL Research Automation", layout="wide")
st.title("BNL Research Automation")
st.caption("BNL Research Search & Screening — Europe PMC discovery, metadata verification, SOP screening, and Excel export.")

search_strings = pd.read_csv(ROOT / "data/search_strings.example.csv")
diseases = pd.read_csv(ROOT / "data/disease_areas.example.csv")["disease_area"].dropna().astype(str).tolist()

if "bnl_run" not in st.session_state:
    st.session_state.bnl_run = None
if "bnl_is_running" not in st.session_state:
    st.session_state.bnl_is_running = False


with st.sidebar:
    st.header("BNL Research Search & Screening")
    if valid_search_strings(search_strings):
        st.success(f"BNL configuration ready: {len(search_strings)}/36 search strings; {len(diseases)} disease areas")
    else:
        st.error("The bundled search-string configuration is invalid. The search cannot start.")
    st.header("Date Range")
    default_end = date.today()
    default_start = default_end - timedelta(days=14)
    start = st.date_input("Start Date", default_start)
    end = st.date_input("End Date", default_end)
    if (end - start).days != 14:
        st.warning("Selected range is not 14 days.")
    st.header("Search configuration")
    limit = st.selectbox("Maximum records per search string", [50, 100, 200], index=1)
    st.caption("Europe PMC requests up to five API pages per string and stops sooner when the selected record cap or available results are reached.")
    provider = ai_provider()
    st.info(f"Screening provider: {provider}" if provider else "No AI screening key configured. Candidates will remain in Needs Manual Review; no scientific rejection is inferred.")


def save_run(state: dict) -> None:
    """Persist partial work before Streamlit reruns or a later query fails."""
    st.session_state.bnl_run = state


def execute() -> None:
    progress_bar = st.progress(0)
    progress_text = st.empty()
    started = time.monotonic()

    def progress(number: int, total: int, query: str) -> None:
        current = st.session_state.bnl_run or {}
        progress_text.write(
            f"Search {number} of {total} — {query}\n\n"
            f"Raw records: {len(current.get('raw', []))} | "
            f"Provider failures: {len(current.get('provider_errors', []))} | "
            f"Elapsed: {time.monotonic() - started:.0f}s"
        )
        progress_bar.progress(number / total)

    st.session_state.bnl_is_running = True
    try:
        save_run(new_run_state(start, end, int(limit), "bnl_research"))
        st.session_state.bnl_run = run_newsletter_search(
            search_strings,
            diseases,
            start,
            end,
            int(limit),
            searchers=(("Europe PMC", search_europe_pmc),),
            progress=progress,
            state_sink=save_run,
            workflow="bnl_research",
            require_google_coverage=False,
        )
        progress_text.success("Search, verification, screening, and export preparation completed. Results remain available while this Streamlit session is open.")
    finally:
        st.session_state.bnl_is_running = False


tabs = st.tabs(["RUN", "CANDIDATES", "INCLUDED", "REJECTED", "NEEDS MANUAL REVIEW", "SEARCH LOG", "EXPORT"])

with tabs[0]:
    st.subheader("BNL Research Search & Screening")
    st.write(f"Inclusive date range: {start.strftime('%d %B %Y')} → {end.strftime('%d %B %Y')}")
    st.caption("The original 36 search strings are executed sequentially and unchanged. Europe PMC is the single automated discovery backend; Crossref is used only to enrich DOI metadata.")
    can_run = valid_search_strings(search_strings) and start <= end
    if st.button("RUN BI-WEEKLY NEWSLETTER", type="primary", disabled=not can_run or st.session_state.bnl_is_running):
        execute()

    run = st.session_state.bnl_run
    if run:
        st.subheader(f"Stored result — {run['run_status']}")
        metrics = st.columns(6)
        metrics[0].metric("Queries attempted", f"{len(run['search_coverage'])}/36")
        metrics[1].metric("Raw results", run["funnel"]["records_retrieved"])
        metrics[2].metric("Unique candidates", run["funnel"]["remaining_after_deduplication"])
        metrics[3].metric("Verified", run["funnel"]["verified_candidates"])
        metrics[4].metric("Included", run["funnel"]["final_included"])
        metrics[5].metric("Manual review", run["funnel"]["awaiting_manual_verification"] + run["funnel"]["pending_screening"])
        st.dataframe(pd.DataFrame([{"Stage": key.replace("_", " ").title(), "Count": run["funnel"][key]} for key in FUNNEL_KEYS]), hide_index=True, use_container_width=True)
        if run["filter_reasons"]:
            st.subheader("Rejection reasons")
            st.dataframe(pd.DataFrame([{"Reason": reason, "Count": count} for reason, count in run["filter_reasons"].items()]), hide_index=True, use_container_width=True)
        if not run["included"]:
            st.warning("No paper currently qualifies for the Included sheet. Use the funnel, Rejected, and Needs Manual Review tabs to see exactly where candidates were lost; the workbook remains available.")
        for warning in run["warnings"]:
            st.warning(warning)
        if run["provider_errors"]:
            st.error(f"{len(run['provider_errors'])} query/provider failures were retained in Search Log. Other queries continued.")


run = st.session_state.bnl_run
if run:
    with tabs[1]:
        st.dataframe(pd.DataFrame(run["candidates"]), hide_index=True, use_container_width=True)
    with tabs[2]:
        final = pd.DataFrame(newsletter_rows(run["included"]), columns=NEWSLETTER_COLUMNS)
        st.dataframe(final, hide_index=True, use_container_width=True)
        for paper in run["included"]:
            if paper.get("paper_url"):
                st.link_button(f"Open source: {paper.get('title', 'Article')}", paper["paper_url"])
    with tabs[3]:
        st.dataframe(pd.DataFrame(run["rejected"]), hide_index=True, use_container_width=True)
    with tabs[4]:
        st.dataframe(pd.DataFrame(run["pending"]), hide_index=True, use_container_width=True)
    with tabs[5]:
        st.dataframe(pd.DataFrame(run["logs"]), hide_index=True, use_container_width=True)
        st.dataframe(pd.DataFrame(run["search_coverage"]), hide_index=True, use_container_width=True)
    with tabs[6]:
        csv, xlsx, markdown, audit = exports(
            run["included"], run["start"], run["end"], candidates=run["candidates"],
            rejected=run["rejected"], pending=run["pending"], search_log=run["logs"],
        )
        st.download_button("Download Newsletter Excel", xlsx, "bnl_newsletter.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("Download Newsletter CSV", csv, "bnl_newsletter.csv", "text/csv")
        st.download_button("Download Newsletter Markdown", markdown, "bnl_newsletter.md", "text/markdown")
        st.download_button("Download Audit CSV", audit, "bnl_audit.csv", "text/csv")
else:
    for tab in tabs[1:]:
        with tab:
            st.info("Run BNL Research Search & Screening to view retained results and exports.")
