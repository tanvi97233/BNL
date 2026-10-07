from datetime import date
from pathlib import Path

from bnl_tool.config import Settings, resolve_date_range
from bnl_tool.dedupe import dedupe_papers
from bnl_tool.fetch import Paper
from bnl_tool.qc import run_qc
from bnl_tool.screen import ScreeningDecision, enforce_sop, is_ahead_of_print, sentence_count
from bnl_tool.search import SearchExecution, has_next_page, load_queries, parse_google_results


def _decision(domain="AUTOIMMUNITY", diagnostics=True, treatment=False):
    return ScreeningDecision(paper_type="Research Paper", peer_reviewed_journal=True, ahead_of_print_or_preprint=False, publication_date_confirmed=True, within_date_range=True, diagnostics_or_biomarkers_primary_focus=diagnostics, treatment_primary_focus=treatment, disease_areas_matched=["Rheumatoid arthritis"], domain=domain, relevant_to_thermo_fisher_immunodiagnostics=True, include=True, summary="One. Two. Three.")


def test_default_end_uses_last_working_day_and_14_day_window():
    start, end = resolve_date_range(None, None, today=date(2026, 10, 10))
    assert (start, end) == (date(2026, 9, 25), date(2026, 10, 9))


def test_local_browser_defaults_are_visible_and_persistent(monkeypatch):
    monkeypatch.delenv("BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("BROWSER_PROFILE_DIR", raising=False)
    settings = Settings.from_env()
    assert not settings.browser_headless
    assert settings.browser_profile_dir.name == "google-profile"


def test_exact_36_queries_and_source_query_preserved():
    queries = load_queries("search_strings.txt")
    assert len(queries) == 36
    assert queries[14] == '(Dermatomyositis" OR "Polymyositis") AND ("Immunoassay" OR "Assay" OR "Diagnostic")'


def test_dedupe_doi_and_fuzzy_title():
    records, log = dedupe_papers([Paper("A diagnostic assay for lupus", "https://one", doi="10.1/a"), Paper("Different", "https://two", doi="10.1/a"), Paper("A diagnostic assay for lupus!", "https://three")])
    assert len(records) == 1 and [row["method"] for row in log] == ["doi", "fuzzy_title"]


def test_summary_and_ahead_of_print_rules():
    assert sentence_count("One. Two! Three?") == 3
    assert is_ahead_of_print(Paper("P", "https://x", volume="1", issue="2", abstract="Published online first."))
    assert not is_ahead_of_print(Paper("P", "https://x", volume="", issue="", abstract="Final article."))


def test_allergy_is_or_and_autoimmunity_treatment_is_excluded():
    paper = Paper("P", "https://x", doi="10.1/x", volume="1", issue="2", publication_date="2026-10-01")
    allowed = ["Rheumatoid arthritis"]
    allergy = _decision("ALLERGY", diagnostics=False, treatment=True)
    assert enforce_sop(paper, allergy, allowed, date(2026, 9, 23), date(2026, 10, 7)).include
    autoimmune = _decision("AUTOIMMUNITY", diagnostics=False, treatment=True)
    assert not enforce_sop(paper, autoimmune, allowed, date(2026, 9, 23), date(2026, 10, 7)).include


def test_google_result_fixture_parsing_and_ranks():
    html = (Path(__file__).parent / "fixtures/google_results.html").read_text()
    hits = parse_google_results(html, query_number=1, query="exact query", page_number=1, google_url="https://google.com/search", timestamp="now")
    assert [(hit.rank, hit.title, hit.url) for hit in hits] == [(1, "Diagnostic assay validation", "https://journal.example.org/article/1"), (2, "Journal title two", "https://news.example.org/item")]
    assert has_next_page(html)


def test_qc_passes_complete_compliant_run():
    paper = Paper("P", "https://x", doi="10.1/x", volume="1", issue="2", publication_date="2026-10-01", abstract="Different abstract text that should not be similar.")
    decision = _decision().model_copy(update={"final_relevance_confirmed": True})
    searches = [SearchExecution(number, f"q{number}", "2026-09-23", "2026-10-07", True) for number in range(1, 37)]
    assert run_qc([paper], {paper.url: decision}, searches, [], [], date(2026, 9, 23), date(2026, 10, 7)).passed
