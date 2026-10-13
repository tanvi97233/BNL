from datetime import date
from io import BytesIO

import pandas as pd
from openpyxl import load_workbook

try:
    from bnl_research_automation.core import NEWSLETTER_COLUMNS, apply_screening, deduplicate, deterministic_status, exports, normalize_title, presentation_title, summary_valid, valid_search_strings
    from bnl_research_automation.pipeline import new_run_state, run_newsletter_search
    from bnl_research_automation.screening import parse_decision, screen_paper
    SCREENING_PATH = "bnl_research_automation.screening"
except ModuleNotFoundError:
    from core import NEWSLETTER_COLUMNS, apply_screening, deduplicate, deterministic_status, exports, normalize_title, presentation_title, summary_valid, valid_search_strings
    from pipeline import new_run_state, run_newsletter_search
    from screening import parse_decision, screen_paper
    SCREENING_PATH = "screening"


def valid_summary():
    return "In a study published in the journal, 'Clinical Immunology' (Impact factor not verified), researchers aimed to assess diagnostic biomarkers in rheumatoid arthritis among adults evaluated for suspected autoimmune disease in routine specialist practice. The study involved prospective analysis of serum samples, standardised laboratory measurements, and clinical records from a clearly described cohort, with biomarker results compared against established diagnostic classification procedures, reference assessments, demographic characteristics, and independently recorded clinical features at the time of evaluation. The results revealed that the evaluated markers distinguished relevant disease groups, supported diagnostic interpretation, showed associations with the study's defined clinical outcomes, and retained practical relevance across prespecified analyses, diverse clinical presentations, and relevant comparison groups under the stated study conditions, although the supplied evidence did not establish claims beyond the clearly reported data provided. The study concluded that these biomarkers may support diagnostic evaluation for rheumatoid arthritis when interpreted with clinical assessment and validated testing, while further independent confirmation is required before routine implementation."


def paper(**more):
    return {"title": "IL-6 biomarker evaluation in rheumatoid arthritis", "abstract": "A diagnostic biomarker study with sufficient reported methods and findings.", "publication_date": "2026-10-01", "source_date": "2026-10-01", "doi": "10.1/test", "pmid": "", "journal": "Clinical Immunology", "source_name": "Clinical Immunology", "paper_url": "https://example.test/article", "volume": "12", "issue": "3", "publication_types": ["Journal Article"], "search_strings_found": ["Search string 1"], "sources_found": ["Europe PMC"], **more}


def decision(**more):
    return {"include": True, "paper_type": "Research Paper", "disease_areas": ["Rheumatoid arthritis"], "diagnostics_or_biomarker_relevant": True, "treatment_relevant": False, "autoimmunity_treatment_exclusion": False, "case_report": False, "preprint_or_online_first": False, "date_valid": True, "peer_reviewed": True, "client_scope_relevant": True, "reason": "", "summary": valid_summary(), **more}


def test_search_string_validation_and_title_normalization():
    assert valid_search_strings(pd.DataFrame({"number": range(1, 37), "search_string": ["x"] * 36}))
    assert not valid_search_strings(pd.DataFrame({"number": range(1, 36), "search_string": ["x"] * 35}))
    assert normalize_title(" Test:  Paper! ") == "test paper"
    assert presentation_title("IL-6 biomarker evaluation in rheumatoid arthritis") == "IL-6 Biomarker Evaluation in Rheumatoid Arthritis"


def test_date_boundaries_case_and_doi_deduplication():
    assert deterministic_status(paper(publication_date="2026-09-23"), date(2026, 9, 23), date(2026, 10, 7))[0] == "eligible"
    assert deterministic_status(paper(publication_date="2026-10-07"), date(2026, 9, 23), date(2026, 10, 7))[0] == "eligible"
    assert deterministic_status(paper(publication_date="2026-08-01"), date(2026, 9, 23), date(2026, 10, 7))[0] == "rejected"
    assert "Case" in deterministic_status(paper(title="A case report"), date(2026, 9, 23), date(2026, 10, 7))[1]
    assert deterministic_status(paper(title=None), date(2026, 9, 23), date(2026, 10, 7))[0] == "rejected"
    assert len(deduplicate([paper(), paper(title="Changed", sources_found=["OpenAlex"])])) == 1


def test_domain_logic_summary_and_ai_json():
    areas = ["Rheumatoid arthritis", "Allergy"]
    assert summary_valid(valid_summary())[0]
    assert not apply_screening(paper(), decision(treatment_relevant=True), areas)[0]
    assert apply_screening(paper(), decision(disease_areas=["Allergy"], diagnostics_or_biomarker_relevant=False, treatment_relevant=True), areas)[0]
    assert parse_decision(__import__("json").dumps(decision()))["include"]


def test_six_column_export_and_audit_retains_url():
    csv, xlsx, markdown, audit = exports([paper(**decision())], date(2026, 9, 23), date(2026, 10, 7))
    assert list(pd.read_csv(__import__("io").BytesIO(csv)).columns) == NEWSLETTER_COLUMNS
    assert b"https://example.test/article" in audit
    workbook = load_workbook(BytesIO(xlsx))
    assert workbook.sheetnames == ["Included Papers", "Rejected Papers", "Pending Review", "Search Log"]
    assert [cell.value for cell in workbook["Included Papers"][1]] == NEWSLETTER_COLUMNS
    assert len(xlsx) > 1000 and "Clinical Immunology" in markdown


def test_preprint_is_pending_and_multiple_area_allergy_is_eligible():
    assert deterministic_status(paper(title="Online first biomarker study"), date(2026, 9, 23), date(2026, 10, 7))[0] == "pending"
    assert deterministic_status(paper(is_preprint=True), date(2026, 9, 23), date(2026, 10, 7))[0] == "rejected"
    areas = ["Allergy", "Rheumatoid arthritis"]
    multi = decision(disease_areas=["Allergy", "Rheumatoid arthritis"], diagnostics_or_biomarker_relevant=False, treatment_relevant=True)
    assert apply_screening(paper(), multi, areas)[0]


def test_pipeline_state_persists_and_tracks_all_36_searches():
    strings = pd.DataFrame({"number": range(1, 37), "search_string": ["diagnostic biomarker"] * 36})
    mock_search = lambda query, start, end, limit, log: [paper()]
    mock_screen = lambda *_: {**decision(), "screening_status": "SCREENED"}
    state = run_newsletter_search(strings, ["Rheumatoid arthritis", "Allergy"], date(2026, 9, 23), date(2026, 10, 7), 50, searchers=(("Mock", mock_search),), screener=mock_screen, crossref=lambda doi, log: {})
    assert state["run_status"] == "COMPLETE"
    assert len(state["search_coverage"]) == 36
    assert state["funnel"]["remaining_after_deduplication"] == 1
    assert len(state["included"]) == 1
    assert new_run_state(date(2026, 9, 23), date(2026, 10, 7), 50)["run_status"] == "NOT_STARTED"


def test_ai_failure_is_pending(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setattr(f"{SCREENING_PATH}.requests.post", lambda *args, **kwargs: (_ for _ in ()).throw(__import__("requests").ConnectionError("offline")))
    outcome = screen_paper(paper(), ["Rheumatoid arthritis"], date(2026, 9, 23), date(2026, 10, 7))
    assert outcome["screening_status"] == "AI SCREENING PENDING"
