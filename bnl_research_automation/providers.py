"""Public literature providers and normal-browser Google-assisted candidate import."""
from __future__ import annotations

import re
import time
import os
from datetime import date, datetime, timezone
from html import unescape
from typing import Callable
from urllib.parse import urlencode

import requests

TIMEOUT = 30
MAX_API_PAGES = 5
USER_AGENT = "BNL-Research-Automation/1.1 (local literature review)"
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


def _request(url: str, *, params: dict | None, log: Callable[[dict], None], provider: str) -> dict:
    started = time.monotonic()
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            log({"provider": provider, "status": "ok", "error": "", "duration_seconds": round(time.monotonic() - started, 2), "request": response.url, "attempt": attempt + 1})
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 2:
                log({"provider": provider, "status": "error", "error": str(exc), "duration_seconds": round(time.monotonic() - started, 2), "request": url, "attempt": attempt + 1})
                return {}
            time.sleep(1.5 * (attempt + 1))
    return {}


def _date(value: object) -> str:
    return str(value)[:10] if value else ""


def _europe_record(item: dict) -> dict:
    journal_info = item.get("journalInfo") or {}
    # A final print/issue date is more authoritative than an online-first
    # record.  Crossref remains the final verification source when a DOI is
    # present; this ordering merely prevents first-publication metadata from
    # becoming an irreversible early rejection.
    source_date = _date(journal_info.get("printPublicationDate") or journal_info.get("electronicPublicationDate") or item.get("firstPublicationDate"))
    return {
        "title": item.get("title") or "", "abstract": item.get("abstractText") or "",
        "authors": [author.get("fullName", "") for author in (item.get("authorList") or {}).get("author", [])],
        "journal": item.get("journalTitle") or "", "source_name": item.get("journalTitle") or "",
        "publication_date": source_date, "source_date": source_date,
        "first_publication_date": _date(item.get("firstPublicationDate")),
        "doi": item.get("doi") or "", "pmid": item.get("pmid") or "",
        "paper_url": f"https://europepmc.org/article/{item.get('source', '')}/{item.get('id', '')}",
        "publication_types": (item.get("pubTypeList") or {}).get("pubType", []) or [],
        "mesh_terms": [term.get("descriptorName", "") for term in (item.get("meshHeadingList") or {}).get("meshHeading", [])],
        "topics": [], "is_open_access": item.get("isOpenAccess"), "source": "Europe PMC",
        "volume": journal_info.get("volume") or "", "issue": journal_info.get("issue") or "", "is_preprint": str(item.get("source", "")).upper() in {"PPR", "MEDRXIV", "BIORXIV"},
    }


def search_europe_pmc(query: str, start_date: date, end_date: date, limit: int, log: Callable[[dict], None]) -> list[dict]:
    """Keep the original Boolean query intact and apply the API date constraint separately."""
    records, cursor = [], "*"
    # Europe PMC's user-facing date filtering is based on FIRST_PDATE.  Using
    # print-only P_PDATE at discovery silently misses electronically published
    # journal papers.  The original query remains untouched in provenance;
    # final issue/date eligibility is enforced after Crossref verification.
    provider_query = f"({query}) AND FIRST_PDATE:[{start_date.isoformat()} TO {end_date.isoformat()}]"
    page_number = 0
    while len(records) < limit and cursor and page_number < MAX_API_PAGES:
        page_number += 1
        payload = _request("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={"query": provider_query, "format": "json", "pageSize": min(100, limit - len(records)), "cursorMark": cursor, "resultType": "core"}, log=log, provider="Europe PMC")
        batch = payload.get("resultList", {}).get("result", [])
        records.extend(_europe_record(item) for item in batch)
        if payload:
            log({"provider": "Europe PMC", "status": "page", "error": "", "duration_seconds": 0, "request": "", "page": page_number, "records": len(batch), "pages_available_or_processed": page_number})
        next_cursor = payload.get("nextCursorMark")
        if not batch or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor; time.sleep(0.2)
    return records[:limit]


def _abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    words = [(position, word) for word, positions in inverted.items() for position in positions]
    return " ".join(word for _, word in sorted(words))


def _openalex_record(item: dict) -> dict:
    doi = (item.get("doi") or "").replace("https://doi.org/", "")
    location = item.get("primary_location") or {}
    host = location.get("source") or {}
    biblio = item.get("biblio") or {}
    return {
        "title": item.get("title") or "", "abstract": _abstract(item.get("abstract_inverted_index")),
        "authors": [author.get("author", {}).get("display_name", "") for author in item.get("authorships", [])],
        "journal": host.get("display_name") or "", "source_name": host.get("display_name") or "",
        "publication_date": _date(item.get("publication_date")), "source_date": _date(item.get("publication_date")),
        "doi": doi, "pmid": "", "paper_url": item.get("doi") or location.get("landing_page_url") or item.get("id", ""),
        "publication_types": [item.get("type") or ""], "mesh_terms": [],
        "topics": [topic.get("display_name", "") for topic in item.get("topics", [])],
        "is_open_access": (item.get("open_access") or {}).get("is_oa"), "source": "OpenAlex",
        "volume": biblio.get("volume") or "", "issue": biblio.get("issue") or "", "is_preprint": item.get("type") == "preprint",
    }


def search_openalex(query: str, start_date: date, end_date: date, limit: int, log: Callable[[dict], None]) -> list[dict]:
    records, cursor = [], "*"
    while len(records) < limit and cursor:
        payload = _request("https://api.openalex.org/works", params={"search": query, "filter": f"from_publication_date:{start_date.isoformat()},to_publication_date:{end_date.isoformat()}", "per-page": min(100, limit - len(records)), "cursor": cursor}, log=log, provider="OpenAlex")
        batch = payload.get("results", [])
        records.extend(_openalex_record(item) for item in batch)
        cursor = payload.get("meta", {}).get("next_cursor")
        if not batch:
            break
        time.sleep(0.2)
    return records[:limit]


def enrich_crossref(doi: str, log: Callable[[dict], None]) -> dict:
    if not doi:
        return {}
    return _request(f"https://api.crossref.org/works/{doi}", params={}, log=log, provider="Crossref").get("message", {})


def crossref_fields(paper: dict, message: dict) -> dict:
    if not message:
        return paper
    updated = dict(paper)
    dates = message.get("published-print") or message.get("published-online") or message.get("issued") or {}
    parts = dates.get("date-parts", [[]])[0] if isinstance(dates, dict) else []
    if parts:
        verified = "-".join(f"{int(part):02d}" if index else str(int(part)) for index, part in enumerate(parts))
        updated["verified_publication_date"] = verified
        updated["source_date"] = verified
    updated["journal"] = (message.get("container-title") or [updated.get("journal", "")])[0]
    updated["source_name"] = updated["journal"] or updated.get("source_name", "")
    updated["volume"] = message.get("volume") or updated.get("volume", "")
    updated["issue"] = message.get("issue") or updated.get("issue", "")
    updated["crossref_final_issue"] = bool(message.get("volume") or message.get("issue"))
    # Preserve the Google-discovered original article URL for traceability;
    # Crossref's URL is supplementary metadata, not a replacement discovery
    # source.
    updated["paper_url"] = updated.get("paper_url") or message.get("URL") or ""
    updated["crossref_url"] = message.get("URL") or updated.get("crossref_url", "")
    return updated


def build_google_search_url(query: str, start: date, end: date) -> str:
    """A normal-browser Google URL; creating it does not claim coverage was completed."""
    return "https://www.google.com/search?" + urlencode({"q": query, "tbs": f"cdr:1,cd_min:{start:%m/%d/%Y},cd_max:{end:%m/%d/%Y}"})


class SerpApiSetupError(RuntimeError):
    pass


def serpapi_setup_error() -> str | None:
    if not os.getenv("SERPAPI_API_KEY", "").strip():
        return "SERPAPI_API_KEY is required. Add it to Streamlit Secrets before running BNL Research Search & Screening."
    return None


def _serpapi_record(item: dict, *, query_number: int, query: str, page: int, google_url: str) -> dict:
    """Normalize an organic Google result without pretending it is paper metadata."""
    link = str(item.get("link") or "")
    return {
        "title": item.get("title") or "", "abstract": item.get("snippet") or "",
        "google_snippet": item.get("snippet") or "", "google_displayed_date": item.get("date") or "",
        "authors": [], "journal": "", "source_name": item.get("displayed_link") or "",
        "publication_date": "", "source_date": "", "doi": "", "pmid": "",
        "paper_url": link, "original_url": link, "publication_types": [], "mesh_terms": [], "topics": [],
        "is_open_access": None, "source": "SerpApi Google", "volume": "", "issue": "", "is_preprint": False,
        "search_strings_found": [f"Search string {query_number}"], "sources_found": ["SerpApi Google"],
        "discovery_query": query, "discovery_query_number": query_number, "discovery_page": page,
        "discovery_rank": item.get("position"), "google_results_url": google_url,
        "raw_result_metadata": item,
    }


def search_serpapi_google(
    query: str,
    start_date: date,
    end_date: date,
    limit: int,
    log: Callable[[dict], None],
    *,
    query_number: int = 0,
    retries: int = 2,
) -> list[dict]:
    """Run up to five sequential SerpApi Google result pages for one exact BNL query."""
    setup_error = serpapi_setup_error()
    if setup_error:
        raise SerpApiSetupError(setup_error)
    api_key = os.environ["SERPAPI_API_KEY"]
    records: list[dict] = []
    # In the unified workflow ``limit`` is the requested maximum page count.
    max_pages = max(1, min(5, int(limit)))
    tbs = f"cdr:1,cd_min:{start_date:%m/%d/%Y},cd_max:{end_date:%m/%d/%Y}"
    for page in range(1, max_pages + 1):
        params = {"engine": "google", "q": query, "tbs": tbs, "start": (page - 1) * 10, "num": 10, "api_key": api_key}
        started = time.monotonic()
        payload: dict | None = None
        last_error = ""
        # A page request is billable.  The UI's safe test mode passes zero
        # retries so it is exactly one request, not an invisible retry loop.
        for attempt in range(max(0, retries) + 1):
            try:
                response = requests.get(SERPAPI_ENDPOINT, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
                http_status = response.status_code
                if response.status_code in {408, 409, 429} or response.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {response.status_code}: transient SerpApi response")
                response.raise_for_status()
                payload = response.json()
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                break
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = str(exc)
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        safe_request = f"engine=google&q=<exact query>&tbs={tbs}&start={(page - 1) * 10}&num=10"
        if payload is None:
            log({
                "provider": "SerpApi Google", "status": "error", "error": last_error,
                "duration_seconds": round(time.monotonic() - started, 2), "request": safe_request,
                "page": page, "start_offset": (page - 1) * 10, "records": 0,
                "organic_results_received": 0, "records_extracted": 0,
                "http_status": locals().get("http_status", ""), "attempts": max(0, retries) + 1,
                "date_filter": tbs, "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            continue
        metadata = payload.get("search_metadata") or {}
        google_url = metadata.get("google_url") or ""
        organic = payload.get("organic_results") or []
        extracted = [_serpapi_record(item, query_number=query_number, query=query, page=page, google_url=google_url) for item in organic if item.get("link")]
        log({
            "provider": "SerpApi Google", "status": "ok", "error": "",
            "duration_seconds": round(time.monotonic() - started, 2), "request": safe_request,
            "page": page, "start_offset": (page - 1) * 10, "records": len(extracted),
            "organic_results_received": len(organic), "records_extracted": len(extracted),
            "http_status": locals().get("http_status", ""), "attempts": attempt + 1,
            "date_filter": tbs, "google_url": google_url,
            "serpapi_status": metadata.get("status", ""),
            "serpapi_error": payload.get("error", ""),
            "returned_query": (payload.get("search_parameters") or {}).get("q", ""),
            "organic_results_state": (payload.get("search_information") or {}).get("organic_results_state", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        records.extend(extracted)
        # No organic results or no next SerpApi page means Google supplied no
        # further page to process.  Never claim pages that were not returned.
        if not organic or not (payload.get("serpapi_pagination") or {}).get("next"):
            break
        time.sleep(float(os.getenv("SERPAPI_DELAY_SECONDS", "0.2")))
    return records


def _meta(html: str, name: str) -> str:
    pattern = rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']'
    match = re.search(pattern, html, flags=re.I)
    return unescape(match.group(1)).strip() if match else ""


def fetch_article_candidate(url: str, log: Callable[[dict], None]) -> dict:
    """Fetch article metadata from a discovered source URL without discarding it on failure."""
    started = time.monotonic()
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        response.raise_for_status()
        html = response.text
        doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", html, flags=re.I)
        title = _meta(html, "citation_title") or _meta(html, "og:title") or ""
        return {"title": title, "abstract": _meta(html, "citation_abstract") or _meta(html, "description"), "authors": [], "journal": _meta(html, "citation_journal_title"), "source_name": _meta(html, "citation_journal_title"), "publication_date": _date(_meta(html, "citation_publication_date")), "source_date": _date(_meta(html, "citation_publication_date")), "doi": _meta(html, "citation_doi") or (doi_match.group(0) if doi_match else ""), "pmid": _meta(html, "citation_pmid"), "paper_url": response.url, "publication_types": [], "mesh_terms": [], "topics": [], "is_open_access": None, "source": "source metadata fetch", "volume": _meta(html, "citation_volume"), "issue": _meta(html, "citation_issue"), "is_preprint": False, "metadata_source_url": response.url}
    except requests.RequestException as exc:
        log({"provider": "Source metadata fetch", "status": "error", "error": str(exc), "duration_seconds": round(time.monotonic() - started, 2), "request": url, "attempt": 1})
        return {"title": "", "paper_url": url, "source": "source metadata fetch", "fetch_error": str(exc), "metadata_source_url": url}


# Backwards-compatible name for the retained legacy/manual modules.
fetch_google_candidate = fetch_article_candidate
