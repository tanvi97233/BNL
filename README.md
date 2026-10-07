# BNL bi-weekly newsletter automation

BNL prepares an auditable Thermo Fisher Immunodiagnostics literature newsletter for autoimmune and allergy evidence. It discovers candidates with Google, fetches journal metadata, verifies Crossref data, screens with Grok plus code-enforced SOP rules, deduplicates, runs QC, and generates a DOCX newsletter and supporting files.

**The search stage uses a real Google Search browser session through Playwright. No SerpAPI or Serper API is used.**

## Architecture

`Playwright/Chromium → google.com → rendered organic results → prefilter → journal page/Crossref → Grok → programmatic SOP gate → dedupe → final Grok relevance gate → QC → DOCX/CSV/JSON/XLSX`

The browser stage uses a dedicated persistent profile under `.cache/google-profile`; it does not use a personal Chrome profile or Google login. Local runs default to a visible, installed Google Chrome/Chromium when available, retain the same profile and search page across strings, establish `google.com` before searching, and use no forced user-agent or fingerprint overrides. It records Google result URLs/timestamps/date-filter evidence, parses rendered organic `h3` results, and follows the visible Next control for up to five pages. Search result parsing is fixture-tested so unit tests do not require live Google.

The nearby `DNLAutomation` project informed the staged orchestration, progress callbacks, structured failure recording and output-oriented workflow. Its Google implementation is Google News RSS, not browser automation, so it was intentionally not copied. No accessible Dexcom project was found during implementation.

## Install

```bash
cd /Users/tanvisidhwani/bnl-tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Set only the model-provider values needed for screening:

```env
XAI_API_KEY=your_xai_key
XAI_MODEL=grok-4
XAI_BASE_URL=https://api.x.ai/v1
```

No Google, SerpAPI, or Serper credential is required. Browser settings include `BROWSER_HEADLESS=false`, `BROWSER_CHANNEL=auto`, `BROWSER_EXECUTABLE_PATH`, `BROWSER_PROFILE_DIR`, `BROWSER_TIMEOUT_MS`, `GOOGLE_DELAY_MS`, and `GOOGLE_HOME_WAIT_MS`. CI explicitly uses headless Playwright Chromium; local visible Chrome is the preferred reference path.

## Run

```bash
python main.py --end-date 2026-10-07
python main.py --start-date 2026-09-23 --end-date 2026-10-07 --max-pages 5
python main.py --end-date 2026-10-07 --resume
python main.py --end-date 2026-10-07 --only-string 7
python main.py --end-date 2026-10-07 --dry-run
```

The command prints `Confirmed date range` before launching Chromium. If omitted, end date is today (or the preceding Friday on weekends); start date is end date minus 14 days. CLI dates override `START_DATE`/`END_DATE`. `--dry-run` uses two strings and one result page each, so its 36-string QC check is expected to fail and create a DRAFT document. `--resume` reuses successful search executions and fetched paper cache keys scoped by query/date range/page count.

Launch the optional UI with `streamlit run app.py`. It provides date controls, page limit, dry run, progress, and downloads.

## SOP controls

The prefilter rejects known news, blog, PR, social, forum and preprint domains but leaves unknown domains eligible for metadata review. All candidates remain in `audit_log.csv` with an exclusion reason.

Grok is called at temperature zero with JSON-only, Pydantic-validated responses and two retries. Code then verifies the journal date, date window, preprint/ahead-of-print status, DOI/title metadata, final volume/issue, configured disease area, paper type and required fields. Conflicts are excluded and written as `conflict: ...`.

Autoimmunity requires diagnostics/biomarkers as the primary focus and excludes treatment-focused work. Allergy uses the SOP exception: diagnostics **or** treatment/immunotherapy papers are eligible; both are not required. A second Grok relevance gate evaluates included records for practical Thermo Fisher Immunodiagnostics relevance.

## Outputs

Every run writes to `output/<run_date>/`:

- `BNL_<start>_to_<end>.docx`, or `_DRAFT.docx` when QC fails
- `audit_log.csv`
- `screening_results.json`
- `qc_report.txt`
- `papers.csv` and `papers.xlsx`
- `search_log.json`
- `dedupe_log.json`

The document has the required header and exactly six paper fields: Serial Number, Title, Disease Area, Paper Type, Summary, and clickable Source Link. QC tests all 13 listed SOP gates; any failed gate marks the report DRAFT.

## GitHub Actions smoke test

The manual workflow at `.github/workflows/bnl.yml` is deliberately limited to source string 1, one Google result page, and the fixed date window ending `2026-10-07`. It installs dependencies and Playwright Chromium, runs tests, then executes exactly:

```bash
python main.py --dry-run --only-string 1 --max-pages 1 --end-date 2026-10-07
```

No secrets are required: dry-run omits xAI screening and the workflow has no SerpAPI/Serper credential. In GitHub, open **Actions → BNL Google smoke test → Run workflow → Run workflow**. Read the job summary for one of `SUCCESSFUL_GOOGLE_SEARCH`, `GOOGLE_BLOCKED`, or `APPLICATION_ERROR`, then download the two artifacts. `GOOGLE_BLOCKED` is reported successfully as an auditable network result; it is not treated as a production-ready search. Do not expand the workflow to all 36 strings until this smoke test has a successful Google-search result.

## Troubleshooting and limitations

- **Google CAPTCHA/unusual traffic:** the run logs `GOOGLE_BLOCKED`, stops safely, and fails QC. It saves the returned HTML, screenshot, title, URL and body excerpt under `.cache/google-blocked/` and in `search_log.json`. Run later or use the visible local browser profile; the tool does not bypass Google controls.
- **Paywalled/JS-heavy papers:** metadata may be incomplete; final publication details are required, so the paper is excluded if they cannot be verified.
- **Crossref:** Crossref enriches/verifies bibliographic data but is never used as the search engine.
- **Grok:** model failures, invalid JSON or summaries outside 3–5 sentences are retried then excluded; a valid xAI-compatible credential is required for full runs.
