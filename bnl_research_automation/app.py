from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Community Cloud runs the command from the repository root even when this
# nested file is selected as the entry point.  Package imports therefore make
# the full application work there, while the fallback preserves ``streamlit
# run app.py`` from this directory for local users.
try:
    from bnl_research_automation.core import NEWSLETTER_COLUMNS, exports, newsletter_rows, valid_search_strings
    from bnl_research_automation.pipeline import FUNNEL_KEYS, new_run_state, run_newsletter_search
    from bnl_research_automation.providers import build_google_search_url, search_europe_pmc, search_openalex
    from bnl_research_automation.screening import ai_provider
    from bnl_research_automation.xai_web import search_grok_web, web_search_setup_error
except ModuleNotFoundError:
    from core import NEWSLETTER_COLUMNS, exports, newsletter_rows, valid_search_strings
    from pipeline import FUNNEL_KEYS, new_run_state, run_newsletter_search
    from providers import build_google_search_url, search_europe_pmc, search_openalex
    from screening import ai_provider
    from xai_web import search_grok_web, web_search_setup_error

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
st.set_page_config(page_title="BNL Research Automation", layout="wide")
st.title("BNL Research Automation")

WORKFLOWS = {
    "literature": "Workflow 1: Literature APIs",
    "google_manual": "Workflow 2: Manual Google Batch Review",
    "grok_web": "Workflow 3: Grok Live Web Search (Experimental)",
}
search_strings = pd.read_csv(ROOT / "data/search_strings.example.csv")
diseases = pd.read_csv(ROOT / "data/disease_areas.example.csv")["disease_area"].dropna().astype(str).tolist()
if "workflow_runs" not in st.session_state:
    st.session_state.workflow_runs = {key: None for key in WORKFLOWS}
if "google_entries" not in st.session_state:
    st.session_state.google_entries = {}
if "google_batch_index" not in st.session_state:
    st.session_state.google_batch_index = 1
if "is_running" not in st.session_state:
    st.session_state.is_running = False

with st.sidebar:
    if valid_search_strings(search_strings):
        st.success(f"BNL configuration ready: {len(search_strings)}/36 search strings; {len(diseases)} disease areas")
    else:
        st.error("The bundled search-string configuration is invalid.")
    workflow = st.radio("Research workflow", list(WORKFLOWS), format_func=lambda key: WORKFLOWS[key])
    st.header("Date Range")
    default_end = date.today(); default_start = default_end - timedelta(days=14)
    start = st.date_input("Start Date", default_start); end = st.date_input("End Date", default_end)
    if (end - start).days != 14:
        st.warning("Selected range is not 14 days.")
    st.header("Configuration")
    limit = st.selectbox("Maximum records per search string", [50, 100, 200], index=1)
    st.info(f"Screening provider: {ai_provider()}" if ai_provider() else "No screening key configured: records requiring AI stay pending.")
    if workflow == "grok_web":
        st.caption("Workflow 3 uses the paid xAI Responses API web_search tool; it never uses the Groq key.")


def active_run() -> dict | None:
    return st.session_state.workflow_runs.get(workflow)


def save_run(state: dict) -> None:
    st.session_state.workflow_runs[workflow] = state


def execute(*, searchers, require_google_coverage: bool) -> None:
    progress_bar = st.progress(0)
    progress_text = st.empty()
    started = __import__("time").monotonic()

    def progress(number: int, total: int, query: str) -> None:
        current = active_run() or {}
        failed = len(current.get("provider_errors", []))
        candidates = len(current.get("raw", []))
        progress_text.write(f"Search {number} of {total} completed — {query}\n\nFailed: {failed} | Candidates: {candidates} | Elapsed: {__import__('time').monotonic() - started:.0f}s")
        progress_bar.progress(number / total)

    st.session_state.is_running = True
    try:
        st.session_state.workflow_runs[workflow] = new_run_state(start, end, limit, workflow)
        st.session_state.workflow_runs[workflow] = run_newsletter_search(
            search_strings, diseases, start, end, limit, google_entries=st.session_state.google_entries if workflow == "google_manual" else {}, progress=progress, state_sink=save_run, searchers=searchers, workflow=workflow, require_google_coverage=require_google_coverage,
        )
        progress_text.success("Run complete. Results are retained for this workflow while the Streamlit session remains open.")
    finally:
        st.session_state.is_running = False


tabs = st.tabs(["RUN", "MANUAL GOOGLE BATCH", "CANDIDATES", "INCLUDED", "REJECTED", "PENDING REVIEW", "SEARCH LOG", "EXPORT"])
with tabs[0]:
    st.subheader(WORKFLOWS[workflow])
    st.write(f"Date range: {start.strftime('%d %B %Y')} → {end.strftime('%d %B %Y')}")
    can_run = valid_search_strings(search_strings) and start <= end
    if workflow == "literature":
        st.caption("Supplementary discovery through Europe PMC and OpenAlex. It does not claim Google SOP coverage.")
        if st.button("RUN LITERATURE DISCOVERY", type="primary", disabled=not can_run or st.session_state.is_running):
            execute(searchers=(("Europe PMC", search_europe_pmc), ("OpenAlex", search_openalex)), require_google_coverage=False)
    elif workflow == "google_manual":
        reviewed = sum(bool(item.get("reviewed")) for item in st.session_state.google_entries.values())
        captured = sum(len(item.get("urls", [])) for item in st.session_state.google_entries.values())
        st.caption(f"Batch progress: {reviewed}/36 Google queries recorded; {captured} candidate URLs captured. Complete the batch in the Manual Google Batch tab, then process captured URLs.")
        if st.button("PROCESS CAPTURED GOOGLE CANDIDATES", type="primary", disabled=not can_run or not st.session_state.google_entries or st.session_state.is_running):
            execute(searchers=(), require_google_coverage=True)
    else:
        setup_error = web_search_setup_error()
        if setup_error:
            st.error(setup_error)
        else:
            st.warning("Experimental paid workflow: each of the 36 sequential xAI requests enables the live web_search tool. Only run when you intend to use xAI API credits.")
        if st.button("RUN GROK LIVE WEB SEARCH", type="primary", disabled=not can_run or bool(setup_error) or st.session_state.is_running):
            def grok_finder(query, query_start, query_end, query_limit, log):
                return search_grok_web(query, query_start, query_end, diseases, query_limit, log)
            execute(searchers=(("xAI Grok Live Web Search", grok_finder),), require_google_coverage=False)

    run = active_run()
    if run:
        st.subheader(f"Stored result — {run['run_status']}")
        complete = sum(all(provider["status"] == "COMPLETE" for provider in item["providers"]) for item in run["search_coverage"])
        metrics = st.columns(5)
        metrics[0].metric("Search strings attempted", f"{len(run['search_coverage'])}/36")
        metrics[1].metric("Completed strings", f"{complete}/36")
        metrics[2].metric("Raw records", run["funnel"]["records_retrieved"])
        metrics[3].metric("Unique candidates", run["funnel"]["remaining_after_deduplication"])
        metrics[4].metric("Included", run["funnel"]["final_included"])
        st.dataframe(pd.DataFrame([{"Stage": key.replace("_", " ").title(), "Count": run["funnel"][key]} for key in FUNNEL_KEYS]), hide_index=True, use_container_width=True)
        for warning in run["warnings"]:
            st.warning(warning)

with tabs[1]:
    st.subheader("Manual Google batch review")
    st.info("This guided workflow opens one exact custom-date Google query at a time in your normal browser. Record each review and its article URLs; after 36 reviews, Workflow 2 fetches, verifies, screens, summarizes, and exports those candidates.")
    number = st.session_state.google_batch_index
    row = search_strings.loc[search_strings["number"] == number].iloc[0]
    query = str(row["search_string"])
    entry = st.session_state.google_entries.get(number, {})
    st.progress((number - 1) / 36, text=f"Manual Google review {number} of 36")
    st.code(query, language=None)
    st.link_button("Open exact date-filtered Google query", build_google_search_url(query, start, end), type="primary")
    pages = st.number_input("Pages reviewed", min_value=0, max_value=5, value=int(entry.get("pages_reviewed", 0)), key=f"batch_pages_{number}")
    urls_text = st.text_area("Candidate article URLs (one per line)", value="\n".join(entry.get("urls", [])), key=f"batch_urls_{number}")
    if st.button("Save this review and continue", type="primary"):
        urls = list(dict.fromkeys(line.strip() for line in urls_text.splitlines() if line.strip().startswith(("http://", "https://"))))
        st.session_state.google_entries[number] = {"reviewed": True, "pages_reviewed": int(pages), "urls": urls, "original_query": query, "start": str(start), "end": str(end)}
        st.session_state.google_batch_index = min(36, number + 1)
        st.rerun()
    if number == 36 and entry.get("reviewed"):
        st.success("All 36 manual Google review steps have been recorded. Select Workflow 2 and click Process Captured Google Candidates.")
    st.dataframe(pd.DataFrame([{"Search string": item, "Reviewed": bool(value.get("reviewed")), "Pages": value.get("pages_reviewed", 0), "Candidate URLs": len(value.get("urls", []))} for item, value in sorted(st.session_state.google_entries.items())]), hide_index=True, use_container_width=True)

run = active_run()
if run:
    with tabs[2]: st.dataframe(pd.DataFrame(run["candidates"]), hide_index=True, use_container_width=True)
    with tabs[3]:
        final = pd.DataFrame(newsletter_rows(run["included"]), columns=NEWSLETTER_COLUMNS)
        st.dataframe(final, hide_index=True, use_container_width=True)
        for paper in run["included"]:
            if paper.get("paper_url"):
                st.link_button(f"Open source: {paper.get('title', 'Article')}", paper["paper_url"])
    with tabs[4]: st.dataframe(pd.DataFrame(run["rejected"]), hide_index=True, use_container_width=True)
    with tabs[5]: st.dataframe(pd.DataFrame(run["pending"]), hide_index=True, use_container_width=True)
    with tabs[6]:
        st.dataframe(pd.DataFrame(run["logs"]), hide_index=True, use_container_width=True)
        st.dataframe(pd.DataFrame(run["search_coverage"]), hide_index=True, use_container_width=True)
    with tabs[7]:
        csv, xlsx, markdown, audit = exports(run["included"], run["start"], run["end"], rejected=run["rejected"], pending=run["pending"], search_log=run["logs"])
        st.download_button("Download Newsletter CSV", csv, "bnl_newsletter.csv", "text/csv")
        st.download_button("Download Newsletter Excel", xlsx, "bnl_newsletter.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("Download Newsletter Markdown", markdown, "bnl_newsletter.md", "text/markdown")
        st.download_button("Download Audit CSV", audit, "bnl_audit.csv", "text/csv")
else:
    for tab in tabs[2:]:
        with tab: st.info("Run the selected workflow to view its retained results and exports.")
