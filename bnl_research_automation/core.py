"""Deterministic BNL validation, presentation, and export helpers."""
from __future__ import annotations

import re
from datetime import date
from io import BytesIO

import pandas as pd

NEWSLETTER_COLUMNS = ["Title", "Summary", "Disease Area", "Date", "Source Name", "Source Date"]
SMALL_TITLE_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "of", "on", "or", "the", "to", "via", "with"}


def text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text(title).lower())).strip()


def presentation_title(title: str | None) -> str:
    """Conservative title case: retain acronyms, existing mixed case, and digit tokens."""
    source = text(title)
    if not source:
        return ""
    words = re.split(r"(\s+)", source)
    output, word_index = [], 0
    for token in words:
        if token.isspace():
            output.append(token); continue
        bare = re.sub(r"^[^\w]*|[^\w]*$", "", token)
        lower = bare.lower()
        preserve = (bare.isupper() and len(bare) > 1) or any(char.isdigit() for char in bare) or (any(char.isupper() for char in bare[1:]))
        if word_index and lower in SMALL_TITLE_WORDS and not preserve:
            output.append(token.lower())
        elif preserve:
            output.append(token)
        else:
            output.append(token[:1].upper() + token[1:])
        word_index += 1
    return "".join(output)


def valid_search_strings(frame: pd.DataFrame) -> bool:
    return set(frame.columns) >= {"number", "search_string"} and len(frame) == 36 and frame["search_string"].astype(str).str.strip().ne("").all() and frame["number"].tolist() == list(range(1, 37))


def parse_publication_date(value: object) -> date | None:
    try:
        return date.fromisoformat(text(value)[:10])
    except ValueError:
        return None


def deterministic_status(paper: dict, start: date, end: date) -> tuple[str, str, str]:
    """Return status, reason, and funnel bucket without treating absent evidence as rejection."""
    title = text(paper.get("title"))
    if not title:
        return "rejected", "Missing usable title", "missing_metadata"
    published = parse_publication_date(paper.get("publication_date"))
    if not published:
        return "pending", "Publication date needs manual verification", "needs_date_verification"
    if not start <= published <= end:
        return "rejected", "Outside selected date range", "outside_date_range"
    if paper.get("is_preprint"):
        return "rejected", "Confirmed preprint", "preprint"
    blob = " ".join([title, text(paper.get("abstract")), " ".join(text(item) for item in (paper.get("publication_types") or []))]).lower()
    if any(term in blob for term in ("preprint", "ahead of print", "online first", "early access", "early view")):
        return "pending", "Possible preprint or non-final publication status", "possible_non_final"
    if any(term in blob for term in ("case report", "case series", "case study")):
        return "rejected", "Case report/series/study", "case_report"
    if any(term in blob for term in ("editorial", "news", "press release", "commentary", "letter to the editor")):
        return "rejected", "Non-paper content", "non_paper"
    return "eligible", "", "eligible_metadata"


def deterministic_filter(paper: dict, start: date, end: date) -> str:
    """Backward-compatible reason-only interface used by earlier callers/tests."""
    return deterministic_status(paper, start, end)[1]


def merge_provenance(canonical: dict, duplicate: dict) -> None:
    for field in ("search_strings_found", "sources_found", "google_candidate_urls"):
        canonical[field] = sorted(set(canonical.get(field, []) or []) | set(duplicate.get(field, []) or []))
    for field in ("abstract", "journal", "paper_url", "source_name", "source_date"):
        if not text(canonical.get(field)) and text(duplicate.get(field)):
            canonical[field] = duplicate[field]


def deduplicate(papers: list[dict]) -> list[dict]:
    kept: list[dict] = []
    by_key: dict[str, dict] = {}
    for paper in papers:
        keys = [
            f"doi:{text(paper.get('doi')).lower()}" if text(paper.get("doi")) else "",
            f"pmid:{text(paper.get('pmid'))}" if text(paper.get("pmid")) else "",
            f"title:{normalize_title(paper.get('title'))}",
        ]
        match = next((by_key[key] for key in keys if key and key in by_key), None)
        if match:
            merge_provenance(match, paper)
            continue
        paper = dict(paper)
        paper["search_strings_found"] = sorted(set(paper.get("search_strings_found", []) or []))
        paper["sources_found"] = sorted(set(paper.get("sources_found", [paper.get("source", "")]) or []))
        kept.append(paper)
        for key in keys:
            if key:
                by_key[key] = paper
    return kept


def final_publication_status(paper: dict) -> tuple[str, str]:
    """Only final issue evidence is eligible; uncertain metadata remains reviewable."""
    if paper.get("is_preprint"):
        return "pending", "Preprint status requires manual verification"
    if text(paper.get("volume")) or text(paper.get("issue")):
        return "verified", ""
    if paper.get("crossref_final_issue"):
        return "verified", ""
    return "pending", "Final journal issue/publication status needs manual verification"


def sentence_count(summary: object) -> int:
    return len([item for item in re.split(r"(?<=[.!?])\s+", text(summary)) if item])


def summary_valid(summary: object) -> tuple[bool, str]:
    value = text(summary)
    if "\n" in str(summary or ""):
        return False, "Summary must be one paragraph"
    count = len(re.findall(r"\b[\w'-]+\b", value))
    if not 160 <= count <= 180:
        return False, f"Summary must contain 160-180 words (has {count})"
    phrases = ["In a study published in the journal", "The study involved", "The results revealed", "The study concluded that"]
    positions = [value.find(phrase) for phrase in phrases]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        return False, "Summary is missing required transition phrases or order"
    return True, ""


def apply_screening(paper: dict, decision: dict, disease_areas: list[str]) -> tuple[bool, str]:
    if not decision.get("include"):
        return False, text(decision.get("reason")) or "AI excluded"
    if decision.get("paper_type") not in {"Research Paper", "Review Paper"}:
        return False, "Ineligible paper type"
    if not decision.get("peer_reviewed") or decision.get("case_report") or decision.get("preprint_or_online_first") or not decision.get("date_valid"):
        return False, "Publication eligibility rule"
    if not decision.get("client_scope_relevant"):
        return False, "Not relevant to Thermo Fisher Immunodiagnostics"
    areas = decision.get("disease_areas", []) or []
    if not areas or any(area not in disease_areas for area in areas):
        return False, "Outside configured disease areas"
    allergy = any("allerg" in area.lower() for area in areas)
    if not allergy and (not decision.get("diagnostics_or_biomarker_relevant") or decision.get("treatment_relevant") or decision.get("autoimmunity_treatment_exclusion")):
        return False, "Autoimmunity treatment/non-diagnostic exclusion"
    if allergy and not (decision.get("diagnostics_or_biomarker_relevant") or decision.get("treatment_relevant")):
        return False, "Allergy lacks diagnostic or treatment relevance"
    valid, reason = summary_valid(decision.get("summary"))
    return (valid, reason)


def newsletter_rows(included: list[dict]) -> list[dict]:
    return [{
        "Title": presentation_title(paper.get("title")),
        "Summary": text(paper.get("summary")),
        "Disease Area": "; ".join(paper.get("disease_areas", []) or []),
        "Date": paper.get("verified_publication_date") or paper.get("publication_date") or "",
        "Source Name": text(paper.get("source_name")) or text(paper.get("journal")),
        "Source Date": paper.get("source_date") or paper.get("verified_publication_date") or paper.get("publication_date") or "",
    } for paper in included]


def _audit_frame(items: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(items).drop(columns=["abstract"], errors="ignore")


def exports(included: list[dict], start: date, end: date, *, rejected: list[dict] | None = None, pending: list[dict] | None = None, search_log: list[dict] | None = None) -> tuple[bytes, bytes, str, bytes]:
    main = pd.DataFrame(newsletter_rows(included), columns=NEWSLETTER_COLUMNS)
    csv = main.to_csv(index=False).encode()
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        main.to_excel(writer, index=False, sheet_name="Included Papers")
        _audit_frame(rejected or []).to_excel(writer, index=False, sheet_name="Rejected Papers")
        _audit_frame(pending or []).to_excel(writer, index=False, sheet_name="Pending Review")
        _audit_frame(search_log or []).to_excel(writer, index=False, sheet_name="Search Log")
    header = f"# Bi-Weekly Newsletter - Research and Review Papers\n\nDate range covered: {start} to {end}\n\nTotal papers included: {len(included)}\n\nPrepared on: {date.today()}\n"
    markdown = header + "\n".join(f"\n## {index}. {row['Title']}\n\n**SUMMARY:** {row['Summary']}\n\n**DISEASE AREA:** {row['Disease Area']}\n\n**DATE:** {row['Date']}\n\n**SOURCE NAME:** {row['Source Name']}\n\n**SOURCE DATE:** {row['Source Date']}\n" for index, row in enumerate(newsletter_rows(included), 1))
    audit = pd.DataFrame(included + (rejected or []) + (pending or [])).to_csv(index=False).encode()
    return csv, buffer.getvalue(), markdown, audit
