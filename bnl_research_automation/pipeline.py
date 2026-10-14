"""Stateful, traceable literature-processing pipeline for the Streamlit application."""
from __future__ import annotations

from datetime import date
from typing import Callable

import pandas as pd

try:  # package imports for repository-wide tests
    from .core import apply_screening, deduplicate, deterministic_status, final_publication_status, parse_publication_date
    from .providers import crossref_fields, enrich_crossref, fetch_google_candidate, search_europe_pmc, search_openalex
    from .screening import screen_paper
except ImportError:  # direct Streamlit execution from this directory
    from core import apply_screening, deduplicate, deterministic_status, final_publication_status, parse_publication_date
    from providers import crossref_fields, enrich_crossref, fetch_google_candidate, search_europe_pmc, search_openalex
    from screening import screen_paper


FUNNEL_KEYS = [
    "records_retrieved", "usable_metadata", "passed_date_checks", "removed_non_paper",
    "removed_preprint_or_early_access", "removed_case_reports", "remaining_after_deduplication",
    "verified_candidates", "sent_to_ai", "accepted_by_ai", "rejected_by_ai", "pending_screening", "awaiting_manual_verification", "final_included",
]


def new_run_state(start: date, end: date, limit: int, workflow: str = "literature") -> dict:
    return {"workflow": workflow, "start": start, "end": end, "limit": limit, "run_status": "NOT_STARTED", "logs": [], "raw": [], "candidates": [], "included": [], "rejected": [], "pending": [], "screening_decisions": {}, "manual_verification": [], "search_coverage": [], "funnel": {key: 0 for key in FUNNEL_KEYS}, "filter_reasons": {}, "provider_errors": [], "warnings": []}


def _paper_key(paper: dict) -> str:
    return paper.get("doi") or paper.get("pmid") or paper.get("paper_url") or paper.get("title", "")


def _record_rejection(state: dict, paper: dict, reason: str, rule: str) -> None:
    state["rejected"].append({**paper, "reason": reason, "rule_triggered": rule, "final_status": "REJECTED"})
    state["filter_reasons"][reason] = state["filter_reasons"].get(reason, 0) + 1


def _record_pending(state: dict, paper: dict, reason: str, status: str = "NEEDS MANUAL VERIFICATION") -> None:
    item = {**paper, "reason": reason, "final_status": status}
    state["pending"].append(item)
    state["manual_verification"].append(item)


def _explicit_sop_exclusion(reason: str) -> bool:
    """Only evidence-backed exclusions become final rejections.

    A model saying it lacks evidence is not evidence that a paper is
    irrelevant.  It remains reviewable instead of disappearing from the
    newsletter audit.
    """
    normalized = reason.lower()
    return any(term in normalized for term in (
        "case report", "case series", "case study", "preprint", "editorial",
        "news", "blog", "press release", "treatment-primary", "treatment focused",
        "therapy-primary", "drug efficacy", "ineligible paper type",
        "autoimmunity treatment/non-diagnostic exclusion",
    ))


def run_newsletter_search(
    search_strings: pd.DataFrame,
    diseases: list[str],
    start: date,
    end: date,
    limit: int,
    *,
    google_entries: dict[int, dict] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    state_sink: Callable[[dict], None] | None = None,
    searchers: tuple[tuple[str, Callable], ...] = (("Europe PMC", search_europe_pmc),),
    screener: Callable = screen_paper,
    crossref: Callable = enrich_crossref,
    workflow: str = "bnl_research",
    require_google_coverage: bool = False,
) -> dict:
    state = new_run_state(start, end, limit, workflow)
    state["run_status"] = "RUNNING"
    google_entries = google_entries or {}

    for _, row in search_strings.iterrows():
        number, query = int(row["number"]), str(row["search_string"])
        coverage = {"search_string_number": number, "original_query": query, "date_range": f"{start} to {end}", "providers": [], "google_status": "NOT REVIEWED", "pages_reviewed": 0, "candidate_urls": [], "errors": []}
        google = google_entries.get(number, {})
        if google:
            coverage.update({"google_status": "MANUALLY REVIEWED" if google.get("reviewed") else "NOT REVIEWED", "pages_reviewed": google.get("pages_reviewed", 0), "candidate_urls": google.get("urls", [])})
            for url in google.get("urls", []):
                paper = fetch_google_candidate(url, state["logs"] .append)
                paper["search_strings_found"] = [f"Search string {number}"]
                paper["sources_found"] = ["Google manual candidate"]
                state["raw"].append(paper)
        for provider_name, finder in searchers:
            local_logs: list[dict] = []
            try:
                found = finder(query, start, end, limit, local_logs.append)
                for entry in local_logs:
                    entry.update({"search_string_number": number, "original_query": query, "provider_query_or_url": entry.get("request", "")})
                    state["logs"].append(entry)
                for paper in found:
                    paper["search_strings_found"] = [f"Search string {number}"]
                    paper["sources_found"] = [provider_name]
                    state["raw"].append(paper)
                capped = len(found) >= limit
                coverage["providers"].append({"provider": provider_name, "status": "COMPLETE", "records": len(found), "result_cap_reached": capped})
                if capped:
                    state["warnings"].append(f"{provider_name} reached the {limit}-record cap for search string {number}; additional records may exist.")
            except Exception as exc:  # provider errors never halt remaining coverage
                coverage["providers"].append({"provider": provider_name, "status": "ERROR", "records": 0, "result_cap_reached": False})
                coverage["errors"].append(str(exc)); state["provider_errors"].append({"provider": provider_name, "search_string_number": number, "error": str(exc)})
        state["search_coverage"].append(coverage)
        if state_sink:
            state_sink(state)
        if progress:
            progress(number, len(search_strings), query)

    state["funnel"]["records_retrieved"] = len(state["raw"])
    preliminary: list[dict] = []
    for paper in state["raw"]:
        status, reason, bucket = deterministic_status(paper, start, end)
        if status == "rejected":
            _record_rejection(state, paper, reason, "Deterministic filter")
            if bucket == "non_paper": state["funnel"]["removed_non_paper"] += 1
            elif bucket == "case_report": state["funnel"]["removed_case_reports"] += 1
            elif bucket == "preprint": state["funnel"]["removed_preprint_or_early_access"] += 1
            continue
        if status == "pending":
            _record_pending(state, paper, reason)
            state["funnel"]["awaiting_manual_verification"] += 1
            if bucket == "possible_non_final": state["funnel"]["removed_preprint_or_early_access"] += 1
            continue
        state["funnel"]["usable_metadata"] += 1
        preliminary.append(paper)

    state["candidates"] = deduplicate(preliminary)
    state["funnel"]["remaining_after_deduplication"] = len(state["candidates"])
    for candidate_index, original_paper in enumerate(state["candidates"]):
        paper = dict(original_paper)
        if paper.get("doi"):
            crossref_logs: list[dict] = []
            paper = crossref_fields(paper, crossref(paper["doi"], crossref_logs.append))
            for entry in crossref_logs:
                entry.update({"search_string_number": "; ".join(paper.get("search_strings_found", [])), "original_query": "metadata verification", "provider_query_or_url": entry.get("request", "")})
                state["logs"].append(entry)
        state["candidates"][candidate_index] = paper
        verified_date = parse_publication_date(paper.get("verified_publication_date") or paper.get("publication_date"))
        if not verified_date:
            _record_pending(state, paper, "Verified publication date is unavailable")
            state["funnel"]["awaiting_manual_verification"] += 1
            continue
        if not start <= verified_date <= end:
            _record_rejection(state, paper, "Verified publication date is outside selected date range", "Final publication verification")
            continue
        paper["verified_publication_date"] = verified_date.isoformat()
        status, reason = final_publication_status(paper)
        if status != "verified":
            _record_pending(state, paper, reason)
            state["funnel"]["awaiting_manual_verification"] += 1
            continue
        state["candidates"][candidate_index] = paper
        state["funnel"]["passed_date_checks"] += 1
        state["funnel"]["verified_candidates"] += 1
        state["funnel"]["sent_to_ai"] += 1
        key = _paper_key(paper)
        decision = state["screening_decisions"].get(key)
        if decision is None:
            decision = screener(paper, diseases, start, end)
            state["screening_decisions"][key] = decision
        item = {**paper, **decision, "disease_areas": decision.get("disease_areas", []), "reason": decision.get("reason", "")}
        if decision.get("screening_status") == "AI SCREENING PENDING":
            _record_pending(state, item, item["reason"], "AI SCREENING PENDING")
            state["funnel"]["pending_screening"] += 1
            continue
        allowed, reason = apply_screening(paper, decision, diseases)
        if allowed:
            state["included"].append({**item, "final_status": "INCLUDED"})
            state["funnel"]["accepted_by_ai"] += 1
        else:
            # Lack of evidence, a model/date disagreement, malformed summary,
            # or unmatched configured area remains reviewable.  Only an
            # explicit exclusion supported by the returned evidence is final.
            combined_reason = reason or item["reason"]
            if not _explicit_sop_exclusion(combined_reason):
                _record_pending(state, item, reason or item["reason"])
                state["funnel"]["awaiting_manual_verification"] += 1
            else:
                _record_rejection(state, item, reason or item["reason"], "AI/programmatic screening")
                state["funnel"]["rejected_by_ai"] += 1
        if state_sink:
            state_sink(state)

    state["funnel"]["final_included"] = len(state["included"])
    if len(state["included"]) < 10:
        state["warnings"].append(f"Low-yield warning: {len(state['included'])} final included papers. This is a diagnostic signal, not an inclusion quota; inspect funnel, pending review, and Google coverage.")
    state["run_status"] = "COMPLETE_WITH_ERRORS" if state["provider_errors"] else "COMPLETE"
    if state_sink:
        state_sink(state)
    return state
