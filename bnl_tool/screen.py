"""SOP-constrained Grok screening and deterministic conflict checks."""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Literal

import requests
from pydantic import BaseModel, Field, field_validator

from .config import Settings
from .fetch import Paper

PaperType = Literal["Research Paper", "Review Paper", "Other"]
Domain = Literal["AUTOIMMUNITY", "ALLERGY", "OTHER"]


class ScreeningDecision(BaseModel):
    paper_type: PaperType
    peer_reviewed_journal: bool
    ahead_of_print_or_preprint: bool
    publication_date_confirmed: bool
    within_date_range: bool
    diagnostics_or_biomarkers_primary_focus: bool
    treatment_primary_focus: bool
    disease_areas_matched: list[str] = Field(default_factory=list)
    domain: Domain
    relevant_to_thermo_fisher_immunodiagnostics: bool
    include: bool
    exclusion_reason: str = ""
    summary: str = ""
    final_relevance_confirmed: bool = False
    final_relevance_reason: str = ""

    @field_validator("summary")
    @classmethod
    def summary_sentence_count(cls, value: str) -> str:
        if value and not 3 <= sentence_count(value) <= 5:
            raise ValueError("summary must contain 3–5 sentences")
        return value


def sentence_count(value: str) -> int:
    return len([sentence for sentence in re.split(r"(?<=[.!?])\s+", value.strip()) if sentence.strip()])


def is_ahead_of_print(paper: Paper) -> bool:
    text = " ".join([paper.title, paper.abstract, paper.online_status, paper.body_text[:6000]]).lower()
    phrases = ("ahead of print", "online first", "online-first", "early access", "early view", "epub ahead", "advance online publication", "preprint")
    # Missing volume/issue is a review flag, not automatically a preprint;
    # final eligibility still requires verifiable issue assignment below.
    return any(phrase in text for phrase in phrases)


def confirmed_journal_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


SYSTEM_PROMPT = """You apply the BNL SOP to journal literature. Return JSON only and use the exact schema requested.
Eligible content must be a peer-reviewed Research Paper or Review Paper (not case report), must not be a preprint/ahead-of-print/online-first item, must have a volume and issue, and must use a journal publication date. Disease areas must exactly come from the allowed list.
For AUTOIMMUNITY, include only diagnostics or biomarkers as the primary focus and exclude treatment-primary papers. For ALLERGY, diagnostics papers OR treatment/immunotherapy papers OR papers with both are eligible. OTHER is excluded. Include only if relevant to Thermo Fisher Immunodiagnostics. Provide a factual 3–5 sentence summary when included; otherwise summary may be empty."""


class GrokScreener:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings, self.session = settings, session or requests.Session()

    def screen(self, paper: Paper, disease_areas: list[str], start_date: date, end_date: date) -> ScreeningDecision:
        evidence = (paper.abstract or paper.body_text or "No retrievable evidence.")[:12000]
        payload = {"model": self.settings.xai_model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"Allowed disease areas: {json.dumps(disease_areas)}\nDate window: {start_date} to {end_date}\nMetadata: title={paper.title}; journal={paper.journal}; DOI={paper.doi}; volume={paper.volume}; issue={paper.issue}; journal publication date={paper.publication_date}\nEvidence: {evidence}"}]}
        last_error = ""
        for _ in range(3):  # Initial request plus two retries.
            try:
                response = self.session.post(f"{self.settings.xai_base_url}/chat/completions", headers={"Authorization": f"Bearer {self.settings.xai_api_key}", "Content-Type": "application/json"}, json=payload, timeout=self.settings.timeout_seconds)
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"]
                decision = ScreeningDecision.model_validate_json(raw)
                return enforce_sop(paper, decision, disease_areas, start_date, end_date)
            except Exception as exc:  # API and validation failure both retry.
                last_error = str(exc)
        return ScreeningDecision(paper_type="Other", peer_reviewed_journal=False, ahead_of_print_or_preprint=True, publication_date_confirmed=False, within_date_range=False, diagnostics_or_biomarkers_primary_focus=False, treatment_primary_focus=False, domain="OTHER", relevant_to_thermo_fisher_immunodiagnostics=False, include=False, exclusion_reason=f"screening failure: {last_error}")


def enforce_sop(paper: Paper, decision: ScreeningDecision, disease_areas: list[str], start_date: date, end_date: date) -> ScreeningDecision:
    journal_date = confirmed_journal_date(paper.publication_date)
    code_ahead = is_ahead_of_print(paper)
    code_date_ok = bool(journal_date and start_date <= journal_date <= end_date)
    allowed = {item.casefold() for item in disease_areas}
    disease_ok = bool(decision.disease_areas_matched) and all(item.casefold() in allowed for item in decision.disease_areas_matched)
    conflicts = []
    if not paper.doi: conflicts.append("DOI missing")
    if decision.ahead_of_print_or_preprint != code_ahead: conflicts.append("ahead-of-print conflict")
    if decision.publication_date_confirmed != bool(journal_date) or decision.within_date_range != code_date_ok: conflicts.append("publication-date conflict")
    if not (paper.volume and paper.issue): conflicts.append("final volume/issue not confirmed")
    eligible = (decision.include and decision.paper_type in ("Research Paper", "Review Paper") and decision.peer_reviewed_journal and not code_ahead and bool(paper.volume and paper.issue) and bool(journal_date) and code_date_ok and disease_ok and decision.relevant_to_thermo_fisher_immunodiagnostics)
    if decision.domain == "AUTOIMMUNITY": eligible = eligible and decision.diagnostics_or_biomarkers_primary_focus and not decision.treatment_primary_focus
    elif decision.domain == "ALLERGY": eligible = eligible and (decision.diagnostics_or_biomarkers_primary_focus or decision.treatment_primary_focus)
    else: eligible = False
    if decision.paper_type == "Other": conflicts.append("paper type is not Research Paper or Review Paper")
    if not eligible:
        reason = "conflict: " + "; ".join(conflicts) if conflicts else decision.exclusion_reason or "SOP eligibility rule not met"
        return decision.model_copy(update={"include": False, "exclusion_reason": reason})
    return decision.model_copy(update={"include": True, "exclusion_reason": ""})


def final_relevance_check(screener: GrokScreener, paper: Paper, decision: ScreeningDecision) -> ScreeningDecision:
    """A second narrow Grok gate for included papers, as required by the SOP."""
    prompt = ("Return JSON only: {\"relevant\": boolean, \"reason\": string}. Is this genuinely relevant to Thermo Fisher "
              "Immunodiagnostics (autoimmune/allergy diagnostics, allergy testing, immunodiagnostic biomarkers, or related technologies)? "
              f"Title: {paper.title}\nAbstract: {paper.abstract[:8000]}\nInitial summary: {decision.summary}")
    try:
        response = screener.session.post(f"{screener.settings.xai_base_url}/chat/completions", headers={"Authorization": f"Bearer {screener.settings.xai_api_key}", "Content-Type": "application/json"}, json={"model": screener.settings.xai_model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": prompt}]}, timeout=screener.settings.timeout_seconds)
        response.raise_for_status(); payload = json.loads(response.json()["choices"][0]["message"]["content"])
        relevant, reason = bool(payload.get("relevant")), str(payload.get("reason", ""))
        if not relevant: return decision.model_copy(update={"include": False, "exclusion_reason": f"final relevance: {reason}", "final_relevance_confirmed": False, "final_relevance_reason": reason})
        return decision.model_copy(update={"final_relevance_confirmed": True, "final_relevance_reason": reason})
    except Exception as exc:
        return decision.model_copy(update={"include": False, "exclusion_reason": f"final relevance failure: {exc}", "final_relevance_confirmed": False})
