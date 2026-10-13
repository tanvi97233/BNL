"""Bounded, validated xAI/Groq screening with explicit pending outcomes."""
from __future__ import annotations

import json
import os
import time
from datetime import date

import requests
from pydantic import BaseModel, Field, ValidationError


class Decision(BaseModel):
    include: bool
    paper_type: str = ""
    disease_areas: list[str] = Field(default_factory=list)
    diagnostics_or_biomarker_relevant: bool = False
    treatment_relevant: bool = False
    autoimmunity_treatment_exclusion: bool = False
    case_report: bool = False
    preprint_or_online_first: bool = False
    date_valid: bool = False
    peer_reviewed: bool = False
    client_scope_relevant: bool = False
    reason: str = ""
    summary: str = ""


def parse_decision(raw: str) -> dict:
    return Decision.model_validate_json(raw).model_dump()


def ai_provider() -> str | None:
    if os.getenv("GROQ_API_KEY", "").strip():
        return "Groq"
    if os.getenv("XAI_API_KEY", "").strip():
        return "xAI/Grok"
    return None


def _ai_config() -> tuple[str, str, str] | None:
    if os.getenv("GROQ_API_KEY", "").strip():
        return os.environ["GROQ_API_KEY"], os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"), os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    if os.getenv("XAI_API_KEY", "").strip():
        return os.environ["XAI_API_KEY"], os.getenv("XAI_MODEL", "grok-3-mini"), os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
    return None


def pending_decision(reason: str) -> dict:
    return {"include": False, "reason": reason, "screening_status": "AI SCREENING PENDING"}


def screen_paper(paper: dict, disease_areas: list[str], start: date, end: date) -> dict:
    config = _ai_config()
    if config is None:
        return pending_decision("AI screening unavailable; manual review required")
    key, model, base_url = config
    journal = paper.get("source_name") or paper.get("journal") or "Impact/source name not verified"
    rules = """Return JSON only. Required keys: include, paper_type, disease_areas, diagnostics_or_biomarker_relevant, treatment_relevant, autoimmunity_treatment_exclusion, case_report, preprint_or_online_first, date_valid, peer_reviewed, client_scope_relevant, reason, summary. Include only Research Paper or Review Paper that are peer-reviewed final journal publications, are in the supplied date range, match supplied disease areas, and are relevant to Thermo Fisher Immunodiagnostics. Autoimmunity requires diagnostics/biomarkers and excludes treatment-primary papers. Allergy permits diagnostics OR treatment. If evidence is insufficient, set include false and reason exactly explaining missing evidence; do not invent facts. For a qualifying paper, write one 160-180 word, single-paragraph summary using exactly this ordered structure: begin 'In a study published in the journal, '[Journal Name]' ([Impact Factor]), researchers aimed to assess...' where Impact Factor is exactly 'Impact factor not verified' unless supplied; later include 'The study involved...', then 'The results revealed...', then 'The study concluded that...'. Use only supplied metadata/abstract. Do not pad or fabricate sample sizes, methods, results, impact factor, or conclusions."""
    payload = {"model": model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": rules}, {"role": "user", "content": json.dumps({"paper": paper, "journal_name": journal, "impact_factor": "Impact factor not verified", "disease_areas": disease_areas, "date_range": [str(start), str(end)]})}]}
    last_error = ""
    for attempt in range(3):
        try:
            response = requests.post(base_url.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=payload, timeout=45)
            if response.status_code in {408, 409, 429} or response.status_code >= 500:
                raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
            decision = parse_decision(response.json()["choices"][0]["message"]["content"])
            decision["screening_status"] = "SCREENED"
            return decision
        except (requests.RequestException, KeyError, ValidationError, ValueError, TypeError) as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    return pending_decision(f"AI screening failed after 3 attempts: {last_error}")
