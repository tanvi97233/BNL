"""Public literature providers and normal-browser Google-assisted candidate import."""
from __future__ import annotations

import re
import time
from datetime import date
from html import unescape
from typing import Callable
from urllib.parse import urlencode

import requests

TIMEOUT = 30
USER_AGENT = "BNL-Research-Automation/1.1 (local literature review)"


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
    return {
        "title": item.get("title") or "", "abstract": item.get("abstractText") or "",
        "authors": [author.get("fullName", "") for author in (item.get("authorList") or {}).get("author", [])],
        "journal": item.get("journalTitle") or "", "source_name": item.get("journalTitle") or "",
        "publication_date": _date(item.get("firstPublicationDate") or journal_info.get("printPublicationDate") or journal_info.get("electronicPublicationDate")),
        "source_date": _date(journal_info.get("printPublicationDate") or journal_info.get("electronicPublicationDate") or item.get("firstPublicationDate")),
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
    # P_PDATE is Europe PMC's publication-date field.  The original Google
    # query remains untouched in provenance; this is the provider-specific
    # date clause needed for bounded API retrieval.
    provider_query = f"({query}) AND P_PDATE:[{start_date.isoformat()} TO {end_date.isoformat()}]"
    while len(records) < limit and cursor:
        payload = _request("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={"query": provider_query, "format": "json", "pageSize": min(100, limit - len(records)), "cursorMark": cursor, "resultType": "core"}, log=log, provider="Europe PMC")
        batch = payload.get("resultList", {}).get("result", [])
        records.extend(_europe_record(item) for item in batch)
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
    updated["paper_url"] = message.get("URL") or updated.get("paper_url", "")
    return updated


def build_google_search_url(query: str, start: date, end: date) -> str:
    """A normal-browser Google URL; creating it does not claim coverage was completed."""
    return "https://www.google.com/search?" + urlencode({"q": query, "tbs": f"cdr:1,cd_min:{start:%m/%d/%Y},cd_max:{end:%m/%d/%Y}"})


def _meta(html: str, name: str) -> str:
    pattern = rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']'
    match = re.search(pattern, html, flags=re.I)
    return unescape(match.group(1)).strip() if match else ""


def fetch_google_candidate(url: str, log: Callable[[dict], None]) -> dict:
    """Fetch a URL supplied from a user-reviewed normal Google session."""
    started = time.monotonic()
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        response.raise_for_status()
        html = response.text
        doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", html, flags=re.I)
        title = _meta(html, "citation_title") or _meta(html, "og:title") or ""
        return {"title": title, "abstract": _meta(html, "citation_abstract") or _meta(html, "description"), "authors": [], "journal": _meta(html, "citation_journal_title"), "source_name": _meta(html, "citation_journal_title"), "publication_date": _date(_meta(html, "citation_publication_date")), "source_date": _date(_meta(html, "citation_publication_date")), "doi": _meta(html, "citation_doi") or (doi_match.group(0) if doi_match else ""), "pmid": "", "paper_url": response.url, "publication_types": [], "mesh_terms": [], "topics": [], "is_open_access": None, "source": "Google manual candidate", "volume": _meta(html, "citation_volume"), "issue": _meta(html, "citation_issue"), "is_preprint": False, "google_candidate_urls": [url]}
    except requests.RequestException as exc:
        log({"provider": "Google manual candidate", "status": "error", "error": str(exc), "duration_seconds": round(time.monotonic() - started, 2), "request": url, "attempt": 1})
        return {"title": "", "paper_url": url, "source": "Google manual candidate", "fetch_error": str(exc), "google_candidate_urls": [url]}
