import json
from datetime import date

import pandas as pd

try:
    from bnl_research_automation.pipeline import run_newsletter_search
    from bnl_research_automation.xai_web import search_grok_web, web_search_setup_error
    XAI_PATH = "bnl_research_automation.xai_web"
except ModuleNotFoundError:
    from pipeline import run_newsletter_search
    from xai_web import search_grok_web, web_search_setup_error
    XAI_PATH = "xai_web"


class FakeResponse:
    status_code = 200
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "server_side_tool_usage": [{"type": "SERVER_SIDE_TOOL_WEB_SEARCH"}],
            "output_text": json.dumps({"candidates": [{"title": "Verified diagnostic study", "article_url": "https://journal.example/paper", "doi": "10.1000/example", "journal_name": "Clinical Immunology", "publication_date": "2026-10-01", "source_publication_date": "2026-10-01", "article_type": "Research Paper", "abstract_or_evidence": "Publisher metadata identifies a diagnostic biomarker study.", "disease_areas": ["Rheumatoid arthritis"], "diagnostics_or_biomarker_relevance": "Diagnostic biomarker evaluation", "qualification_reason": "Potentially eligible", "metadata_source_url": "https://journal.example/paper", "volume": "10", "issue": "2", "final_issue_confirmed": True}]})
        }


def test_xai_web_request_enables_web_search_and_normalizes(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("XAI_WEB_MODEL", "grok-test-web")
    captured = {}
    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return FakeResponse()
    monkeypatch.setattr(f"{XAI_PATH}.requests.post", fake_post)
    logs = []
    results = search_grok_web("exact source string", date(2026, 9, 23), date(2026, 10, 7), ["Rheumatoid arthritis"], 50, logs.append)
    assert captured["tools"] == [{"type": "web_search"}]
    assert results[0]["paper_url"] == "https://journal.example/paper"
    assert logs[0]["web_search_confirmed"] is True


def test_xai_setup_missing_key_is_clear(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert "XAI_API_KEY" in web_search_setup_error()


def test_36_sequential_searches_keep_partial_results_after_failure():
    strings = pd.DataFrame({"number": range(1, 37), "search_string": [f"query {number}" for number in range(1, 37)]})
    called = []
    def finder(query, start, end, limit, log):
        called.append(query)
        if query == "query 18":
            raise RuntimeError("temporary provider failure")
        return [{"title": f"Paper {query}", "abstract": "", "publication_date": "2026-10-01", "source_date": "2026-10-01", "doi": f"10.1/{query}", "journal": "Journal", "source_name": "Journal", "paper_url": "https://example.test", "volume": "1", "issue": "1", "publication_types": ["Research Paper"]}]
    state = run_newsletter_search(strings, ["Allergy"], date(2026, 9, 23), date(2026, 10, 7), 50, searchers=(("Mock web", finder),), screener=lambda *args: {"include": False, "reason": "Insufficient evidence for qualification", "screening_status": "SCREENED"}, crossref=lambda *_: {})
    assert called == [f"query {number}" for number in range(1, 37)]
    assert len(state["search_coverage"]) == 36
    assert state["search_coverage"][17]["providers"][0]["status"] == "ERROR"
    assert state["funnel"]["records_retrieved"] == 35
