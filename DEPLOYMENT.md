# Streamlit Community Cloud deployment

Deploy the complete BNL application by selecting this repository file as the
**Main file path**:

```text
bnl_research_automation/app.py
```

Do not select the repository-root `app.py`. It is the retained legacy
Playwright/Google SOP interface and is not the complete BNL Research
Automation UI.

Community Cloud detects `bnl_research_automation/requirements.txt` because it
is alongside the selected entry point. That application has three workflows
and does not start Playwright or require a Chromium binary:

1. Literature APIs
2. Manual Google Batch Review
3. Grok Live Web Search (Experimental)

## Secrets

In **App settings → Secrets**, add only the credentials for the workflows you
intend to use. For example:

```toml
GROQ_API_KEY = "..."
GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Required only for Workflow 3 (xAI live web search; may incur API charges)
XAI_API_KEY = "..."
XAI_WEB_MODEL = "grok-4.7"
XAI_BASE_URL = "https://api.x.ai/v1"
```

The app remains usable without either key; records needing AI screening are
shown as pending rather than silently rejected.

## Legacy root application

The root `app.py` remains for the legacy browser-based Google workflow. It
uses Playwright and requires a matching Chromium download via:

```bash
python -m playwright install chromium
```

Community Cloud does not run that command merely because `playwright` appears
in `requirements.txt`. It is therefore not the supported Cloud entry point.
Use the full application path above for Community Cloud.
