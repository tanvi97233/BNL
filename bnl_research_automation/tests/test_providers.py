from datetime import date

import pytest

try:
    from bnl_research_automation import providers
    PROVIDERS_PATH = "bnl_research_automation.providers"
except ModuleNotFoundError:
    import providers
    PROVIDERS_PATH = "providers"


class Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_serpapi_preserves_exact_query_date_filter_and_pagination(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "secret-test-key")
    requests = []

    def fake_get(url, *, params, **kwargs):
        requests.append(params)
        page = len(requests)
        return Response({
            "search_metadata": {"status": "Success", "google_url": f"https://www.google.com/search?start={(page - 1) * 10}"},
            "search_parameters": {"q": source_query},
            "search_information": {"organic_results_state": "Results for exact query"},
            "organic_results": [{"position": 1, "title": f"Paper {page}", "link": f"https://publisher.example/{page}", "snippet": "candidate snippet", "date": "Oct 1, 2026"}],
            "serpapi_pagination": {"next": "yes"} if page < 3 else {},
        })

    monkeypatch.setattr(f"{PROVIDERS_PATH}.requests.get", fake_get)
    source_query = '("Autoimmunity" OR "Autoimmune") AND ("Diagnostic")'
    logs = []
    results = providers.search_serpapi_google(source_query, date(2026, 9, 23), date(2026, 10, 7), 5, logs.append, query_number=1)
    assert [request["start"] for request in requests] == [0, 10, 20]
    assert all(request["q"] == source_query for request in requests)
    assert all(request["tbs"] == "cdr:1,cd_min:09/23/2026,cd_max:10/07/2026" for request in requests)
    assert len(results) == 3
    assert results[0]["discovery_query"] == source_query
    assert results[0]["discovery_page"] == 1
    assert results[0]["discovery_rank"] == 1
    assert "secret-test-key" not in str(logs)
    assert logs[0]["http_status"] == 200
    assert logs[0]["organic_results_received"] == 1
    assert logs[0]["records_extracted"] == 1
    assert logs[0]["returned_query"] == source_query


def test_serpapi_empty_organic_results_is_logged_not_scientific_rejection(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "secret-test-key")
    monkeypatch.setattr(f"{PROVIDERS_PATH}.requests.get", lambda *args, **kwargs: Response({
        "search_metadata": {"status": "Success"},
        "search_parameters": {"q": "exact query"},
        "search_information": {"organic_results_state": "No results"},
        "organic_results": [],
    }))
    logs = []
    results = providers.search_serpapi_google("exact query", date(2026, 9, 23), date(2026, 10, 7), 1, logs.append, retries=0)
    assert results == []
    assert logs[0]["status"] == "ok"
    assert logs[0]["organic_results_received"] == 0
    assert logs[0]["records_extracted"] == 0
    assert logs[0]["attempts"] == 1


def test_serpapi_error_safe_mode_makes_one_request_and_logs_error(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "secret-test-key")
    calls = []

    class Failure:
        status_code = 429
        def raise_for_status(self):
            raise __import__("requests").HTTPError("HTTP 429")
        def json(self):
            return {"error": "rate limit"}

    def fake_get(*args, **kwargs):
        calls.append(1)
        return Failure()

    monkeypatch.setattr(f"{PROVIDERS_PATH}.requests.get", fake_get)
    logs = []
    assert providers.search_serpapi_google("exact query", date(2026, 9, 23), date(2026, 10, 7), 1, logs.append, retries=0) == []
    assert len(calls) == 1
    assert logs[0]["status"] == "error"
    assert logs[0]["http_status"] == 429


def test_serpapi_key_is_required(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    assert "SERPAPI_API_KEY" in providers.serpapi_setup_error()
    with pytest.raises(providers.SerpApiSetupError):
        providers.search_serpapi_google("query", date(2026, 1, 1), date(2026, 1, 2), 1, lambda _: None)
