"""DOI-first and fuzzy-title record linkage."""
from __future__ import annotations

from rapidfuzz.fuzz import ratio

from .fetch import Paper


def dedupe_papers(papers: list[Paper]) -> tuple[list[Paper], list[dict]]:
    kept: list[Paper] = []; duplicates: list[dict] = []
    for paper in papers:
        matched = next((existing for existing in kept if paper.doi and existing.doi and paper.doi.casefold() == existing.doi.casefold()), None)
        if not matched:
            matched = next((existing for existing in kept if ratio(paper.title.casefold(), existing.title.casefold()) >= 95), None)
        if matched:
            matched.search_hits.extend(paper.search_hits)
            duplicates.append({"duplicate_title": paper.title, "kept_title": matched.title, "method": "doi" if paper.doi and paper.doi.casefold() == matched.doi.casefold() else "fuzzy_title"})
        else:
            kept.append(paper)
    return kept, duplicates
