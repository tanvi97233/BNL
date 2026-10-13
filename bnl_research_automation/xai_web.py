"""Experimental xAI Responses API workflow with explicit live web-search evidence."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from typing import Callable

import requests
from pydantic import BaseModel, Field, ValidationError


class WebCandidate(BaseModel):
    title: str
    article_url: str
    doi: str = ""
    pmid: str = ""
    journal_name: str = ""
    publication_date: str = ""
    source_publication_date: str = ""
    article_type: str = ""
    abstract_or_evidence: str = ""
    disease_areas: list[str] = Field(default_factory=list)
    diagnostics_or_biomarker_relevance: str = ""
    qualification_reason: str = ""
    metadata_source_url: str = ""
    volume: str = ""
    issue: str = ""
    final_issue_confirmed: bool = False


class WebSearchPayload(BaseModel):
    candidates: list[WebCandidate] = Field(default_factory=list)


class XAIWebSearchError(RuntimeError):
    pass


def web_search_setup_error() -> str | None:
    if not os.getenv("XAI_API_KEY", "").strip():
        return "XAI_API_KEY is required for Workflow 3. A Groq API key cannot enable xAI live web search."
    if not os.getenv("XAI_WEB_MODEL", "").strip():
        return "XAI_WEB_MODEL is required and must support the xAI Responses API web_search tool."
    return None


def _response_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    fragments: list[str] = []
    for item in payload.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                fragments.append(content["text"])
    return "\n".join(fragments)


def _web_search_confirmed(payload: dict) -> bool:
    usage = payload.get("server_side_tool_usage") or []
    if isinstance(usage, dict):
        usage = [usage]
    if any("WEB_SEARCH" in str(item).upper() for item in usage):
        return True
    return any("web_search" in str(item.get("type", "")).lower() for item in payload.get("output", []) if isinstance(item, dict))


def _strip_code_fence(value: str) -> str:
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    return value.strip()


def _prompt(query: str, start: date, end: date, disease_areas: list[str]) -> str:
    return f"""Use live web search to find real publicly accessible scientific article records for this exact BNL source query, without rewriting it: {query}

Selected inclusive publication window: {start.isoformat()} to {end.isoformat()}.
Approved disease areas: {json.dumps(disease_areas, ensure_ascii=False)}.

Find research papers or review papers only. Return candidate records, not news, blogs, press releases, preprints, editorials, case reports, case series, or case studies. Autoimmunity must be primarily diagnostic, diagnostic-method, diagnostic-performance, or diagnostic-biomarker focused; treatment-primary autoimmunity is ineligible. Allergy diagnostics and therapeutics can qualify. Do not invent metadata. Prefer publisher/journal, PubMed, DOI, or other authoritative bibliographic pages.

Return JSON only as {{"candidates":[...]}}. Every candidate requires title, article_url, journal_name, publication_date, article_type, abstract_or_evidence, disease_areas, diagnostics_or_biomarker_relevance, qualification_reason, and metadata_source_url. Include DOI, PMID, source_publication_date, volume, issue, and final_issue_confirmed only when source-supported. If no source-supported candidates are found, return {{"candidates":[]}}."""


def search_grok_web(query: str, start: date, end: date, disease_areas: list[str], limit: int, log: Callable[[dict], None]) -> list[dict]:
    """Run one explicitly web-enabled xAI request and normalize its candidate records."""
    error = web_search_setup_error()
    if error:
        raise XAIWebSearchError(error)
    model = os.environ["XAI_WEB_MODEL"]
    base_url = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
    request_body = {"model": model, "input": [{"role": "user", "content": _prompt(query, start, end, disease_areas)}], "tools": [{"type": "web_search"}], "temperature": 0}
    last_error = ""
    for attempt in range(3):
        started = time.monotonic()
        try:
            response = requests.post(f"{base_url}/responses", headers={"Authorization": f"Bearer {os.environ['XAI_API_KEY']}", "Content-Type": "application/json"}, json=request_body, timeout=90)
            if response.status_code in {408, 409, 429} or response.status_code >= 500:
                raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
            payload = response.json()
            if not _web_search_confirmed(payload):
                raise XAIWebSearchError("xAI response did not confirm a live web_search tool invocation")
            parsed = WebSearchPayload.model_validate_json(_strip_code_fence(_response_text(payload)))
            log({"provider": "xAI Grok Live Web Search", "status": "ok", "error": "", "duration_seconds": round(time.monotonic() - started, 2), "request": "xAI Responses API / web_search", "attempt": attempt + 1, "web_search_confirmed": True})
            records = []
            for candidate in parsed.candidates[:limit]:
                if not candidate.article_url.startswith(("http://", "https://")):
                    continue
                records.append({"title": candidate.title, "abstract": candidate.abstract_or_evidence, "authors": [], "journal": candidate.journal_name, "source_name": candidate.journal_name, "publication_date": candidate.publication_date, "source_date": candidate.source_publication_date or candidate.publication_date, "doi": candidate.doi.removeprefix("https://doi.org/"), "pmid": candidate.pmid, "paper_url": candidate.article_url, "publication_types": [candidate.article_type], "mesh_terms": [], "topics": [], "is_open_access": None, "source": "xAI Grok Live Web Search", "volume": candidate.volume, "issue": candidate.issue, "crossref_final_issue": candidate.final_issue_confirmed, "metadata_source_url": candidate.metadata_source_url, "grok_web_reason": candidate.qualification_reason, "grok_web_disease_areas": candidate.disease_areas, "google_candidate_urls": []})
            return records
        except (requests.RequestException, ValidationError, ValueError, TypeError, XAIWebSearchError) as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    log({"provider": "xAI Grok Live Web Search", "status": "error", "error": f"Live web search failed after 3 attempts: {last_error}", "duration_seconds": 0, "request": "xAI Responses API / web_search", "attempt": 3, "web_search_confirmed": False})
    raise XAIWebSearchError(f"Live web search failed after 3 attempts: {last_error}")
