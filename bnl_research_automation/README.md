# BNL Research Automation

Local Streamlit application for the Bi-Weekly Newsletter research-paper workflow. It preserves the 36 approved BNL queries and disease areas in `data/`, uses Europe PMC as its single automated discovery backend, verifies DOI metadata through Crossref when available, and supports Groq/xAI screening.

## Run

```bash
cd bnl_research_automation
source .venv/bin/activate
pip install -r requirements.txt
python -m streamlit run app.py
```

Create `.env` from `.env.example` and configure either `GROQ_API_KEY` or `XAI_API_KEY` for automatic screening. API use may require credits. Without a key, records remain in **Pending Review** rather than being treated as irrelevant.

## Streamlit Community Cloud

Select **`bnl_research_automation/app.py`** as the Main file path. This is the
complete BNL application; it provides all three workflows and all result and
export tabs. It deliberately does not use Playwright, so no Chromium browser
installation is required for this deployment. See the repository
[`DEPLOYMENT.md`](../DEPLOYMENT.md) for Secrets and redeployment steps.

## Single workflow

Click **Run Bi-Weekly Newsletter** after choosing the dates. The application runs all 36 original queries sequentially through Europe PMC, preserving each source query and request URL in Search Log. Europe PMC paginates up to five API pages per query and respects the configured record cap. Crossref only enriches DOI metadata; it is not a discovery provider.

Use `GROQ_API_KEY` for Groq screening or `XAI_API_KEY` for xAI/Grok screening. Both may incur API charges. Without a screening key, candidates remain in **Needs Manual Review**; an API/model failure is never treated as scientific rejection.

## What happens in a run

For every original query, the app calls Europe PMC, records the actual provider URL/status, normalizes records, applies bounded result limits, deduplicates by DOI/PMID/title, checks date/content metadata, enriches DOI records through Crossref, then performs AI and programmatic SOP checks.

The Run tab retains the completed funnel, provider errors, warnings, records, decisions, and exports in Streamlit session state while the browser session remains open. The results do not rerun merely because the user changes tabs.

## Scope and limitation

No Google Search API is configured. This deployable workflow makes no claim that it completed a Google search; it is a Europe PMC scientific-literature workflow. The remaining Google/browser modules are retained in the repository for legacy local use but are not part of the Streamlit application.

## Exports

The `Included Papers` worksheet and newsletter CSV contain exactly:

1. Title
2. Summary
3. Disease Area
4. Date
5. Source Name
6. Source Date

The Excel workbook contains `Included`, `Candidates`, `Rejected`, `Needs Manual Review`, and `Search Log` sheets. The separate audit CSV retains URLs, identifiers, provenance, and screening details.

Included summaries must be one paragraph, 160–180 words, and use the required BNL transition phrases. Journal impact factor is displayed as `Impact factor not verified` unless a reliable verification source has been integrated; the app never invents it.

## Limitations

Europe PMC's first-publication date and OpenAlex's publication date are discovery metadata, not conclusive proof of final issue assignment. The app uses volume/issue or Crossref final-issue evidence where available; uncertain records go to **Pending Review**, not the newsletter. Provider outages, result caps, unknown final status, missing abstracts, and AI failures remain visible in the funnel and audit data.
