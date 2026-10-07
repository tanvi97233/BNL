"""Real Google Search discovery through a Playwright Chromium session.

No SERP provider is used.  The parser is intentionally separate from browser
control so fixture tests can verify result extraction without live Google.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout, sync_playwright

from .config import Settings

DENYLIST_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "x.com", "twitter.com", "reddit.com", "quora.com",
    "medrxiv.org", "biorxiv.org", "researchsquare.com", "ssrn.com", "arxiv.org", "preprints.org",
    "prnewswire.com", "businesswire.com", "globenewswire.com", "eurekalert.org", "newswire.com",
    "fiercebiotech.com", "medtechdive.com", "biospace.com", "mobihealthnews.com", "healthcareitnews.com",
    "medium.com", "substack.com", "blogspot.com", "wordpress.com", "tumblr.com",
}


@dataclass
class SearchHit:
    query_number: int
    query: str
    page: int
    rank: int
    title: str
    url: str
    snippet: str = ""
    visible_text: str = ""
    google_results_url: str = ""
    timestamp: str = ""
    prefilter_reason: str = ""

    def asdict(self) -> dict[str, object]: return asdict(self)


@dataclass
class SearchExecution:
    query_number: int
    query: str
    start_date: str
    end_date: str
    date_filter_applied: bool
    google_url: str = ""
    timestamp: str = ""
    pages_processed: int = 0
    status: str = "COMPLETE"  # COMPLETE | FAILED | GOOGLE_BLOCKED
    error: str = ""
    diagnostics: dict = field(default_factory=dict)
    page_log: list[dict] = field(default_factory=list)
    hits: list[SearchHit] = field(default_factory=list)

    def asdict(self) -> dict:
        value = asdict(self)
        return value

    @classmethod
    def fromdict(cls, value: dict) -> "SearchExecution":
        value = dict(value); value["hits"] = [SearchHit(**hit) for hit in value.get("hits", [])]
        return cls(**value)


def load_queries(path: str) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle if line.strip()]


def deny_reason(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    if any(host == domain or host.endswith(f".{domain}") for domain in DENYLIST_DOMAINS):
        return f"prefilter: denylisted source domain ({host})"
    return ""


def parse_google_results(html: str, *, query_number: int, query: str, page_number: int, google_url: str, timestamp: str) -> list[SearchHit]:
    """Extract rendered organic ``a > h3`` results, ignoring Google widgets/ads."""
    soup = BeautifulSoup(html, "html.parser")
    hits: list[SearchHit] = []; seen: set[str] = set()
    root = soup.select_one("#search") or soup
    for heading in root.select("h3"):
        anchor = heading.find_parent("a", href=True)
        if not anchor: continue
        href = anchor["href"]
        host = urlparse(href).netloc.lower()
        if not href.startswith(("http://", "https://")) or host.endswith("google.com") or href in seen: continue
        # A nearby result card retains Google's displayed text and snippet.
        card = heading.find_parent("div")
        for _ in range(4):
            if card and len(card.get_text(" ", strip=True)) > len(heading.get_text(" ", strip=True)) + 30: break
            card = card.parent if card else None
        visible = card.get_text(" ", strip=True) if card else heading.get_text(" ", strip=True)
        title = heading.get_text(" ", strip=True)
        snippet = visible.replace(title, "", 1).strip()[:2000]
        seen.add(href)
        hits.append(SearchHit(query_number, query, page_number, len(hits) + 1, title, href, snippet, visible, google_url, timestamp, deny_reason(href)))
    return hits


def has_next_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return bool(soup.select_one("a#pnnext, a[aria-label='Next page'], a[aria-label='Next']") or soup.find("a", string=lambda x: bool(x and x.strip() == "Next")))


def _is_google_blocked(page) -> bool:
    text = page.locator("body").inner_text(timeout=5_000).lower()
    return "unusual traffic" in text or "our systems have detected" in text or "recaptcha" in text


class GoogleBrowserSearcher:
    def __init__(self, settings: Settings, *, progress=print):
        self.settings, self.progress = settings, progress
        self._playwright = self._browser = self._context = None
        self._search_page = None
        self._google_session_established = False

    def __enter__(self) -> "GoogleBrowserSearcher":
        self.settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        # A dedicated persistent profile preserves benign consent/cookie state;
        # it never uses a personal Chrome profile or Google login.
        launch_options = {"headless": self.settings.browser_headless, "viewport": {"width": 1440, "height": 1100}, "locale": "en-US"}
        executable = self._normal_browser_executable()
        if executable: launch_options["executable_path"] = executable
        elif self.settings.browser_channel in {"chrome", "msedge"}: launch_options["channel"] = self.settings.browser_channel
        self._context = self._playwright.chromium.launch_persistent_context(str(self.settings.browser_profile_dir), **launch_options)
        self._context.set_default_timeout(self.settings.browser_timeout_ms)
        self._search_page = self._context.pages[0] if self._context.pages else self._context.new_page()
        return self

    def __exit__(self, *_):
        if self._context: self._context.close()
        if self._playwright: self._playwright.stop()

    def _cache_path(self, query: str, start: date, end: date, pages: int) -> Path:
        key = hashlib.sha256(f"{query}\0{start}\0{end}\0{pages}".encode()).hexdigest()
        return self.settings.cache_dir / "search" / f"{key}.json"

    def search(self, query_number: int, query: str, start: date, end: date, max_pages: int, *, resume: bool = False) -> SearchExecution:
        cache = self._cache_path(query, start, end, max_pages); cache.parent.mkdir(parents=True, exist_ok=True)
        if resume and cache.exists():
            cached = SearchExecution.fromdict(json.loads(cache.read_text(encoding="utf-8")))
            if cached.status == "COMPLETE": return cached
        execution = SearchExecution(query_number, query, start.isoformat(), end.isoformat(), False, timestamp=datetime.now(timezone.utc).isoformat())
        page = self._search_page
        try:
            self._submit_exact_query(page, query)
            if _is_google_blocked(page):
                execution.status = "GOOGLE_BLOCKED"; execution.error = "Google CAPTCHA/unusual-traffic page detected"; execution.diagnostics = self._capture_block_diagnostics(page); return execution
            execution.date_filter_applied = self._apply_custom_date_range(page, start, end)
            execution.google_url = page.url
            if not execution.date_filter_applied:
                execution.status = "FAILED"; execution.error = "Google custom date filter could not be confirmed"; return execution
            for page_number in range(1, max_pages + 1):
                if _is_google_blocked(page):
                    execution.status = "GOOGLE_BLOCKED"; execution.error = "Google CAPTCHA/unusual-traffic page detected"; execution.diagnostics = self._capture_block_diagnostics(page); break
                now = datetime.now(timezone.utc).isoformat()
                hits = parse_google_results(page.content(), query_number=query_number, query=query, page_number=page_number, google_url=page.url, timestamp=now)
                execution.hits.extend(hits); execution.pages_processed += 1
                next_link = self._next_link(page)
                execution.page_log.append({"page": page_number, "result_count": len(hits), "next_available": bool(next_link), "url": page.url, "timestamp": now})
                if not next_link or page_number == max_pages: break
                next_link.click(); page.wait_for_load_state("domcontentloaded"); page.wait_for_timeout(self.settings.google_delay_ms)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            execution.status = "FAILED"; execution.error = str(exc)
        finally:
            cache.write_text(json.dumps(execution.asdict(), ensure_ascii=False), encoding="utf-8")
        return execution

    def _submit_exact_query(self, page, query: str) -> None:
        if not self._google_session_established:
            page.goto("https://www.google.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(self.settings.google_home_wait_ms)
            self._google_session_established = True
        search = page.locator('textarea[name="q"], input[name="q"]').first
        search.fill(query)
        if search.input_value() != query: raise PlaywrightError("Google query field did not retain exact search string")
        search.press("Enter"); page.wait_for_load_state("domcontentloaded"); page.wait_for_timeout(self.settings.google_delay_ms)

    def _normal_browser_executable(self) -> str:
        candidates = [self.settings.browser_executable_path]
        if self.settings.browser_channel == "auto":
            candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium"]
        for candidate in candidates:
            if candidate and Path(candidate).is_file(): return candidate
        return ""

    def _capture_block_diagnostics(self, page) -> dict:
        """Capture evidence only; never solve or bypass a Google challenge."""
        directory = self.settings.cache_dir / "google-blocked"; directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        html_path, screenshot_path = directory / f"{stamp}.html", directory / f"{stamp}.png"
        try: html_path.write_text(page.content(), encoding="utf-8")
        except Exception: html_path = None
        try: page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception: screenshot_path = None
        try: text = page.locator("body").inner_text(timeout=5_000)[:2000]
        except Exception: text = ""
        return {"url": page.url, "title": page.title(), "body_excerpt": text, "html_path": str(html_path) if html_path else "", "screenshot_path": str(screenshot_path) if screenshot_path else "", "headless": self.settings.browser_headless, "profile_dir": str(self.settings.browser_profile_dir), "executable": self._normal_browser_executable() or self.settings.browser_channel}

    def _apply_custom_date_range(self, page, start: date, end: date) -> bool:
        """Use Google's Tools UI, with Google's own tbs URL as a guarded fallback."""
        try:
            page.get_by_role("button", name="Tools").click()
        except PlaywrightError:
            try: page.get_by_text("Tools", exact=True).click()
            except PlaywrightError: pass
        try:
            page.get_by_text("Any time", exact=True).click()
            page.get_by_text("Custom range...", exact=True).click()
            inputs = page.locator('input[type="text"], input[type="date"]')
            count = inputs.count()
            if count >= 2:
                inputs.nth(count - 2).fill(start.strftime("%m/%d/%Y")); inputs.nth(count - 1).fill(end.strftime("%m/%d/%Y"))
                page.get_by_role("button", name="Go").click(); page.wait_for_load_state("domcontentloaded"); page.wait_for_timeout(self.settings.google_delay_ms)
                if "cdr:1" in page.url: return True
        except PlaywrightError:
            pass
        # Google itself emits this custom-range parameter; this is not a SERP API.
        from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
        parsed = urlparse(page.url); params = parse_qs(parsed.query); params["tbs"] = [f"cdr:1,cd_min:{start.strftime('%m/%d/%Y')},cd_max:{end.strftime('%m/%d/%Y')}"]
        page.goto(urlunparse(parsed._replace(query=urlencode(params, doseq=True))), wait_until="domcontentloaded"); page.wait_for_timeout(self.settings.google_delay_ms)
        return "cdr%3A1" in page.url or "cdr:1" in page.url

    @staticmethod
    def _next_link(page):
        for locator in (page.get_by_role("link", name="Next"), page.locator("a#pnnext"), page.locator('a[aria-label="Next page"]')):
            try:
                if locator.count() and locator.first.is_visible(): return locator.first
            except PlaywrightError: continue
        return None


def unique_urls(hits: list[SearchHit]) -> list[SearchHit]:
    """Keep one fetch per URL; dedupe later retains all search provenance."""
    seen: set[str] = set(); output: list[SearchHit] = []
    for hit in hits:
        key = hit.url.rstrip("/").casefold()
        if key not in seen: seen.add(key); output.append(hit)
    return output
