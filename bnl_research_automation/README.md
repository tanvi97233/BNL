# BNL Research Automation

Local Streamlit application for the Bi-Weekly Newsletter research-paper workflow. It preserves the 36 approved BNL queries and disease areas in `data/`, uses Europe PMC and OpenAlex as supplementary literature discovery sources, and supports Groq/xAI screening.

## Run

```bash
cd bnl_research_automation
source .venv/bin/activate
pip install -r requirements.txt
python -m streamlit run app.py
```

Create `.env` from `.env.example` and configure either `GROQ_API_KEY` or `XAI_API_KEY` for automatic screening. API use may require credits. Without a key, records remain in **Pending Review** rather than being treated as irrelevant.

## Workflows

1. **Literature APIs** — existing Europe PMC/OpenAlex discovery.
2. **Manual Google Batch Review** — 36 guided normal-browser Google reviews. The app opens each exact, custom-date URL one at a time, saves the pasted article URLs, then processes all captured URLs through the same metadata, Crossref, screening, summary, and export pipeline.
3. **Grok Live Web Search (Experimental)** — sends each of the 36 source strings sequentially to xAI's Responses API with its `web_search` tool enabled. It is distinct from Groq and requires an xAI account/key with a compatible model and available API credits:

```text
XAI_API_KEY=...
XAI_WEB_MODEL=grok-4.7
XAI_BASE_URL=https://api.x.ai/v1
```

The workflow does not silently fall back to text-only generation. It requires evidence that xAI's web-search tool was invoked, records individual failures, retains completed strings after partial failure, and then sends returned candidates through the existing verification and SOP gates.

## What happens in a run

For every original query, the app calls Europe PMC and OpenAlex sequentially, records the actual provider URL/status, normalizes records, applies bounded result limits, deduplicates by DOI/PMID/title, checks date/content metadata, enriches DOI records through Crossref, then performs AI and programmatic SOP checks.

The Run tab retains the completed funnel, provider errors, warnings, records, decisions, and exports in Streamlit session state while the browser session remains open. The results do not rerun merely because the user changes tabs.

## Google coverage

No Google Search API is configured. The app does **not** pretend that Europe PMC/OpenAlex complete the Google-based SOP. The manual batch workflow records all 36 reviews individually, including reviewed-page count and candidate URLs. It never bypasses CAPTCHAs or other Google controls.

## Exports

The `Included Papers` worksheet and newsletter CSV contain exactly:

1. Title
2. Summary
3. Disease Area
4. Date
5. Source Name
6. Source Date

The Excel workbook also includes audit-only `Rejected Papers`, `Pending Review`, and `Search Log` sheets. The separate audit CSV retains URLs, identifiers, provenance, and screening details.

Included summaries must be one paragraph, 160–180 words, and use the required BNL transition phrases. Journal impact factor is displayed as `Impact factor not verified` unless a reliable verification source has been integrated; the app never invents it.

## Limitations

Europe PMC's first-publication date and OpenAlex's publication date are discovery metadata, not conclusive proof of final issue assignment. The app uses volume/issue or Crossref final-issue evidence where available; uncertain records go to **Pending Review**, not the newsletter. Provider outages, result caps, unknown final status, missing abstracts, and AI failures remain visible in the funnel and audit data.
