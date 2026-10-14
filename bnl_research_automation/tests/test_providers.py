from datetime import date

try:
    from bnl_research_automation import providers
    PROVIDERS_PATH = "bnl_research_automation.providers"
except ModuleNotFoundError:
    import providers
    PROVIDERS_PATH = "providers"


class Response:
    def __init__(self, payload, url="https://www.ebi.ac.uk/europepmc/webservices/rest/search"):
        self._payload = payload
        self.url = url

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_europe_pmc_preserves_query_and_uses_first_publication_discovery_date(monkeypatch):
    captured = []

    def fake_get(url, *, params, **kwargs):
        captured.append(params)
        return Response({"resultList": {"result": []}})

    monkeypatch.setattr(f"{PROVIDERS_PATH}.requests.get", fake_get)
    source_query = '("Autoimmune") AND ("Diagnostic")'
    logs = []
    assert providers.search_europe_pmc(source_query, date(2026, 9, 23), date(2026, 10, 7), 50, logs.append) == []
    assert source_query in captured[0]["query"]
    assert "FIRST_PDATE:[2026-09-23 TO 2026-10-07]" in captured[0]["query"]
    assert "P_PDATE" not in captured[0]["query"]
