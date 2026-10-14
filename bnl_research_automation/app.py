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
    from bnl_research_automation.providers import search_serpapi_google, serpapi_setup_error
    from bnl_research_automation.screening import ai_provider
except ModuleNotFoundError:  # Local: python -m streamlit run app.py
    from core import NEWSLETTER_COLUMNS, exports, newsletter_rows, valid_search_strings
    from pipeline import FUNNEL_KEYS, new_run_state, run_newsletter_search
    from providers import search_serpapi_google, serpapi_setup_error
    from screening import ai_provider


ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
st.set_page_config(page_title="BNL Research Automation", layout="wide")
st.title("BNL Research Automation")
st.caption("BNL Research Search & Screening — Google discovery via SerpApi, metadata verification, SOP screening, and Excel export.")

search_strings = pd.read_csv(ROOT / "data/search_strings.example.csv")
diseases = pd.read_csv(ROOT / "data/disease_areas.example.csv")["disease_area"].dropna().astype(str).tolist()

if "bnl_run" not in st.session_state:
    st.session_state.bnl_run = None
if "bnl_is_running" not in st.session_state:
    st.session_state.bnl_is_running = False
if "bnl_selected_strings" not in st.session_state:
    st.session_state.bnl_selected_strings = {1}


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
    with st.expander("Select search strings (36)", expanded=True):
        first, second = st.columns(2)
        if first.button("Select All"):
            st.session_state.bnl_selected_strings = set(search_strings["number"].tolist())
            for number in search_strings["number"]:
                st.session_state[f"query_select_{number}"] = True
            st.rerun()
        if second.button("Clear All"):
            st.session_state.bnl_selected_strings = set()
            for number in search_strings["number"]:
                st.session_state[f"query_select_{number}"] = False
            st.rerun()
        for _, row in search_strings.iterrows():
            number = int(row["number"])
            key = f"query_select_{number}"
            if key not in st.session_state:
                st.session_state[key] = number in st.session_state.bnl_selected_strings
            st.checkbox(f"{number}. {row['search_string']}", key=key)
        st.session_state.bnl_selected_strings = {int(number) for number in search_strings["number"] if st.session_state.get(f"query_select_{number}", False)}
    selected_numbers = sorted(st.session_state.bnl_selected_strings)
    st.write(f"Search strings selected: {len(selected_numbers)}/36")
    limit = st.selectbox("Maximum Google result pages per selected search string", [1, 2, 3, 4, 5], index=0)
    retry_failed_pages = st.checkbox("Retry transient failed pages", value=False, help="Retries can consume additional SerpApi searches.")
    retries = 2 if retry_failed_pages else 0
    st.caption("The default is one page per selected query with no automatic retry.")
    active_search_strings = search_strings.loc[search_strings["number"].isin(selected_numbers)].copy()
    baseline_requests = len(active_search_strings) * int(limit)
    estimate = baseline_requests * (retries + 1)
    st.info(f"Request estimate: {baseline_requests} baseline request(s)" + ("; no automatic retries." if not retry_failed_pages else f"; up to {estimate} if every requested page needs retries."))
    search_setup_error = serpapi_setup_error()
    if search_setup_error:
        st.error(search_setup_error)
    provider = ai_provider()
    st.info(f"Screening provider: {provider}" if provider else "No AI screening key configured. Candidates will remain in Needs Manual Review; no scientific rejection is inferred.")


def save_run(state: dict) -> None:
    """Persist partial work before Streamlit reruns or a later query fails."""
    st.session_state.bnl_run = state
    successful = [entry for entry in state.get("logs", []) if entry.get("provider") == "SerpApi Google" and entry.get("status") == "ok"]
    if successful:
        st.session_state.bnl_last_serpapi_success = successful[-1].get("timestamp", "")


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
        st.session_state.bnl_run["queries_planned"] = len(active_search_strings)
        st.session_state.bnl_run["retry_failed_pages"] = retry_failed_pages
        completed_run = run_newsletter_search(
            active_search_strings,
            diseases,
            start,
            end,
            int(limit),
            searchers=(("SerpApi Google", lambda query, begin, finish, pages, log: search_serpapi_google(query, begin, finish, pages, log, retries=retries)),),
            progress=progress,
            state_sink=save_run,
            workflow="bnl_research",
            require_google_coverage=False,
        )
        completed_run["queries_planned"] = len(active_search_strings)
        completed_run["retry_failed_pages"] = retry_failed_pages
        st.session_state.bnl_run = completed_run
        progress_text.success("Search, verification, screening, and export preparation completed. Results remain available while this Streamlit session is open.")
    finally:
        st.session_state.bnl_is_running = False


tabs = st.tabs(["RUN", "RAW RESULTS", "CANDIDATES", "INCLUDED", "REJECTED", "NEEDS MANUAL REVIEW", "SEARCH LOG", "EXPORT"])

with tabs[0]:
    st.subheader("BNL Research Search & Screening")
    st.write(f"Inclusive date range: {start.strftime('%d %B %Y')} → {end.strftime('%d %B %Y')}")
    st.caption("The original search strings are executed sequentially and unchanged through Google Search via SerpApi. Crossref is used only to enrich DOI metadata.")
    last_success = st.session_state.get("bnl_last_serpapi_success")
    previous_errors = (st.session_state.bnl_run or {}).get("provider_errors", [])
    if search_setup_error:
        st.error("SerpApi not configured")
    elif previous_errors:
        st.error("SerpApi error — see Search Log for the retained page-level error details.")
    elif last_success:
        st.success("SerpApi connected")
    else:
        st.info("SerpApi configured; not yet tested. A network/API request is made only when you press Run.")
    if last_success:
        st.caption(f"Last successful SerpApi page: {last_success}")
    selection_error = "Select at least one search string." if not len(active_search_strings) else ""
    if selection_error:
        st.warning(selection_error)
    can_run = valid_search_strings(search_strings) and start <= end and not search_setup_error and not selection_error
    run_label = "RUN BNL SEARCH & SCREENING"
    if st.button(run_label, type="primary", disabled=not can_run or st.session_state.bnl_is_running):
        execute()

    run = st.session_state.bnl_run
    if run:
        funnel = {key: run.get("funnel", {}).get(key, 0) for key in FUNNEL_KEYS}
        st.subheader(f"Stored result — {run['run_status']}")
        metrics = st.columns(6)
        metrics[0].metric("Queries attempted", f"{len(run['search_coverage'])}/{run.get('queries_planned', len(run['search_coverage']))}")
        metrics[1].metric("Raw organic results", funnel["raw_organic_results"])
        metrics[2].metric("Unique candidates", funnel["remaining_after_deduplication"])
        metrics[3].metric("Verified", funnel["verified_candidates"])
        metrics[4].metric("Included", funnel["final_included"])
        metrics[5].metric("Manual review", funnel["awaiting_manual_verification"] + funnel["pending_screening"])
        st.dataframe(pd.DataFrame([{"Stage": key.replace("_", " ").title(), "Count": funnel[key]} for key in FUNNEL_KEYS]), hide_index=True, use_container_width=True)
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
        st.caption("Raw Google organic results, retained before deduplication and source verification.")
        raw_columns = ["title", "paper_url", "google_snippet", "google_displayed_date", "discovery_query_number", "discovery_query", "discovery_page", "discovery_rank"]
        st.dataframe(pd.DataFrame(run["raw"]).reindex(columns=raw_columns), hide_index=True, use_container_width=True)
    with tabs[2]:
        st.dataframe(pd.DataFrame(run["candidates"]), hide_index=True, use_container_width=True)
    with tabs[3]:
        final = pd.DataFrame(newsletter_rows(run["included"]), columns=NEWSLETTER_COLUMNS)
        st.dataframe(final, hide_index=True, use_container_width=True)
        for paper in run["included"]:
            if paper.get("paper_url"):
                st.link_button(f"Open source: {paper.get('title', 'Article')}", paper["paper_url"])
    with tabs[4]:
        st.dataframe(pd.DataFrame(run["rejected"]), hide_index=True, use_container_width=True)
    with tabs[5]:
        st.dataframe(pd.DataFrame(run["pending"]), hide_index=True, use_container_width=True)
    with tabs[6]:
        st.dataframe(pd.DataFrame(run["logs"]), hide_index=True, use_container_width=True)
        st.dataframe(pd.DataFrame(run["search_coverage"]), hide_index=True, use_container_width=True)
    with tabs[7]:
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
