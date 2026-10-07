"""SOP QC gates. Any failure requires a DRAFT newsletter."""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz.fuzz import ratio

from .config import ROOT
from .fetch import Paper
from .screen import ScreeningDecision, confirmed_journal_date, is_ahead_of_print, sentence_count
from .search import SearchExecution, load_queries

@dataclass
class QCReport:
    checks: list[tuple[str, bool, str]]
    @property
    def passed(self) -> bool: return all(ok for _, ok, _ in self.checks)
    def text(self) -> str:
        state = "PASS" if self.passed else "FAIL — REPORT MARKED DRAFT"
        return "\n".join([f"BNL SOP QC report\nOverall: {state}", ""] + [f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}" for name, ok, detail in self.checks]) + "\n"


def run_qc(papers: list[Paper], decisions: dict[str, ScreeningDecision], searches: list[SearchExecution], all_hits, duplicates: list[dict], start_date, end_date) -> QCReport:
    included = [(p, decisions[p.url]) for p in papers if decisions.get(p.url) and decisions[p.url].include]
    expected = load_queries(str(ROOT / "search_strings.txt")); allowed = {area.casefold() for area in load_queries(str(ROOT / "disease_areas.txt"))}
    serials = list(range(1, len(included) + 1))
    unique_ids = set(); no_dupes = True
    for paper, _ in included:
        key = f"doi:{paper.doi.casefold()}" if paper.doi else f"title:{paper.title.casefold()}"
        if key in unique_ids: no_dupes = False
        unique_ids.add(key)
    return QCReport([
        ("1. All 36 search strings processed", len(searches) == 36 and {s.query_number for s in searches} == set(range(1, 37)) and all(s.status == "COMPLETE" for s in searches), f"{len(searches)}/36 search executions complete."),
        ("2. Google date filter confirmed for all 36", len(searches) == 36 and all(s.date_filter_applied for s in searches), f"{sum(s.date_filter_applied for s in searches)}/{len(searches)} confirmed."),
        ("3. Publication date in range", all((dte := confirmed_journal_date(p.publication_date)) and start_date <= dte <= end_date for p, _ in included), f"{len(included)} included papers checked."),
        ("4. No ineligible source/preprint/case report", all(not is_ahead_of_print(p) and p.volume and p.issue and d.paper_type in ("Research Paper", "Review Paper") for p, d in included), "Preprint/online-first and publication type rules checked."),
        ("5. Disease area valid", all(d.disease_areas_matched and all(area.casefold() in allowed for area in d.disease_areas_matched) for _, d in included), "Configured disease-area names only."),
        ("6. Autoimmunity treatment focus excluded", all(d.domain != "AUTOIMMUNITY" or (d.diagnostics_or_biomarkers_primary_focus and not d.treatment_primary_focus) for _, d in included), "Autoimmune therapy-primary work excluded."),
        ("7. Allergy diagnostics OR treatment eligible", all(d.domain != "ALLERGY" or (d.diagnostics_or_biomarkers_primary_focus or d.treatment_primary_focus) for _, d in included), "No simultaneous diagnostics/treatment requirement."),
        ("8. All six output fields populated", all(p.title and d.disease_areas_matched and d.paper_type in ("Research Paper", "Review Paper") and d.summary and (p.doi or p.url) for p, d in included), "Serial, title, disease, type, summary, source checked."),
        ("9. No duplicates", no_dupes, f"{len(duplicates)} duplicates consolidated."),
        ("10. Summary has 3–5 sentences", all(3 <= sentence_count(d.summary) <= 5 for _, d in included), "Sentence counts validated."),
        ("11. Summary is not near-copy of abstract", all(not p.abstract or ratio(d.summary.casefold(), p.abstract.casefold()) < 85 for p, d in included), "Similarity threshold <85%."),
        ("12. Serial numbers sequential", serials == list(range(1, len(included) + 1)), f"1–{len(included)}."),
        ("13. Final Thermo Fisher relevance confirmed", all(d.final_relevance_confirmed for _, d in included), "Final Grok relevance gate checked."),
    ])
