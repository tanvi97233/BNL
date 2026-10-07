"""Paper-page evidence extraction, Crossref verification, and disk caching."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from html import unescape
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .config import Settings
from .search import SearchHit

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)


@dataclass
class Paper:
    title: str
    url: str
    search_hits: list[dict] = field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    journal: str = ""
    doi: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publication_date: str = ""
    online_status: str = ""
    body_text: str = ""
    crossref: dict = field(default_factory=dict)
    fetch_error: str = ""

    def asdict(self) -> dict: return asdict(self)
    @classmethod
    def fromdict(cls, value: dict) -> "Paper": return cls(**{key: value.get(key, field.default_factory() if callable(field.default_factory) else field.default) for key, field in cls.__dataclass_fields__.items()})


def _meta(soup: BeautifulSoup, names: list[str]) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"): return unescape(tag["content"]).strip()
    return ""


def _doi(value: str) -> str:
    match = DOI_RE.search(value)
    return match.group(0).rstrip(".,;) ") if match else ""


def _date_from_crossref(record: dict) -> str:
    value = record.get("published-print") or record.get("published-online") or record.get("issued") or {}
    parts = value.get("date-parts", [[]])[0]
    return "-".join(str(part).zfill(2) if index else str(part) for index, part in enumerate(parts))


class PaperFetcher:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings, self.session = settings, session or requests.Session()
        self.session.headers.update({"User-Agent": "BNL-literature-monitor/0.2"})
        self.settings.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, hit: SearchHit) -> Path:
        return self.settings.cache_dir / f"paper_{hashlib.sha256(hit.url.encode()).hexdigest()}.json"

    def fetch(self, hit: SearchHit, *, resume: bool = False) -> Paper:
        cached = self._cache_path(hit)
        if resume and cached.exists():
            paper = Paper.fromdict(json.loads(cached.read_text(encoding="utf-8")))
            paper.search_hits.append(hit.asdict())
            return paper
        paper = Paper(title=hit.title, url=hit.url, search_hits=[hit.asdict()])
        try:
            response = None
            for attempt in range(3):
                try:
                    response = self.session.get(hit.url, timeout=self.settings.timeout_seconds)
                    response.raise_for_status(); break
                except requests.RequestException:
                    if attempt == 2: raise
            response.raise_for_status()
            self._extract_html(paper, response.text)
        except requests.RequestException as exc:
            paper.fetch_error = str(exc)
            try:
                # A bounded fallback for JavaScript-rendered publisher pages.
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True); page = browser.new_page()
                    page.goto(hit.url, wait_until="domcontentloaded", timeout=self.settings.timeout_seconds * 1000)
                    self._extract_html(paper, page.content()); browser.close()
                paper.fetch_error = ""
            except Exception as fallback_exc:
                paper.fetch_error = f"requests: {exc}; playwright fallback: {fallback_exc}"
        self.enrich_crossref(paper)
        cached.write_text(json.dumps(paper.asdict(), ensure_ascii=False), encoding="utf-8")
        return paper

    @staticmethod
    def _extract_html(paper: Paper, html: str) -> None:
        soup = BeautifulSoup(html, "html.parser")
        paper.title = _meta(soup, ["citation_title", "og:title", "dc.title"]) or (soup.title.get_text(" ", strip=True) if soup.title else paper.title)
        paper.abstract = _meta(soup, ["citation_abstract", "description", "og:description", "dc.description"])
        paper.keywords = [item.strip() for item in _meta(soup, ["citation_keywords", "keywords", "dc.subject"]).split(",") if item.strip()]
        paper.journal = _meta(soup, ["citation_journal_title", "prism.publicationName"])
        paper.doi = _doi(_meta(soup, ["citation_doi", "dc.identifier", "prism.doi"]) or html)
        paper.volume = _meta(soup, ["citation_volume", "prism.volume"]); paper.issue = _meta(soup, ["citation_issue", "prism.number"])
        paper.pages = _meta(soup, ["citation_firstpage", "citation_lastpage", "prism:pageRange"])
        paper.publication_date = _meta(soup, ["citation_publication_date", "citation_online_date", "dc.date", "prism.publicationDate"])
        paper.online_status = _meta(soup, ["citation_status", "prism:publicationStatus"])
        for tag in soup(["script", "style", "noscript"]): tag.decompose()
        paper.body_text = soup.get_text(" ", strip=True)[:30000]

    def enrich_crossref(self, paper: Paper) -> None:
        try:
            endpoint = f"https://api.crossref.org/works/{paper.doi}" if paper.doi else "https://api.crossref.org/works"
            kwargs = {} if paper.doi else {"params": {"query.bibliographic": paper.title, "rows": 1}}
            message = self.session.get(endpoint, timeout=self.settings.timeout_seconds, **kwargs).json().get("message", {})
            record = message if paper.doi else message.get("items", [{}])[0]
            paper.crossref = record
            paper.doi = record.get("DOI", paper.doi)
            paper.title = (record.get("title") or [paper.title])[0] or paper.title
            paper.journal = (record.get("container-title") or [paper.journal])[0] or paper.journal
            paper.volume = str(record.get("volume") or paper.volume or "")
            paper.issue = str(record.get("issue") or paper.issue or "")
            paper.publication_date = _date_from_crossref(record) or paper.publication_date
        except (requests.RequestException, ValueError, IndexError, KeyError) as exc:
            paper.crossref = {"error": str(exc)}
