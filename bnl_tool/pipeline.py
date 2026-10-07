"""BNL orchestration: browser Google → papers → SOP → outputs."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from .config import ROOT, Settings
from .dedupe import dedupe_papers
from .fetch import PaperFetcher
from .qc import QCReport, run_qc
from .report import write_audit_csv, write_docx, write_papers_exports
from .screen import GrokScreener, final_relevance_check
from .search import GoogleBrowserSearcher, SearchExecution, load_queries, unique_urls

Progress = Callable[[str], None]

@dataclass
class RunResult:
    output_dir: Path; report_path: Path; audit_path: Path; qc_path: Path; qc: QCReport


def run_pipeline(settings: Settings, start_date: date, end_date: date, *, max_pages: int = 5, dry_run: bool = False, resume: bool = False, only_string: int | None = None, progress: Progress = print) -> RunResult:
    queries = load_queries(str(ROOT / "search_strings.txt")); disease_areas = load_queries(str(ROOT / "disease_areas.txt"))
    if len(queries) != 36: raise RuntimeError(f"search_strings.txt must contain exactly 36 strings; found {len(queries)}")
    indexed = list(enumerate(queries, 1))
    if only_string is not None:
        if not 1 <= only_string <= len(indexed): raise ValueError("--only-string must be 1–36")
        indexed = [indexed[only_string - 1]]
    elif dry_run: indexed = indexed[:2]; max_pages = 1
    run_dir = settings.output_dir / date.today().isoformat(); run_dir.mkdir(parents=True, exist_ok=True)
    search_executions: list[SearchExecution] = []
    with GoogleBrowserSearcher(settings, progress=progress) as searcher:
        for _, (number, query) in enumerate(indexed, 1):
            progress(f"String {number}/36")
            execution = searcher.search(number, query, start_date, end_date, max_pages, resume=resume)
            search_executions.append(execution)
            if execution.status == "GOOGLE_BLOCKED":
                progress("GOOGLE_BLOCKED — stopping without bypassing Google security controls")
                break
    all_hits = [hit for execution in search_executions for hit in execution.hits]
    candidates = [hit for hit in all_hits if not hit.prefilter_reason]
    fetcher = PaperFetcher(settings); papers = [fetcher.fetch(hit, resume=resume) for hit in unique_urls(candidates)]
    papers, duplicates = dedupe_papers(papers)
    decisions = {}
    if settings.xai_api_key:
        screener = GrokScreener(settings)
        for index, paper in enumerate(papers, 1):
            progress(f"Screening {index}/{len(papers)}")
            decision = screener.screen(paper, disease_areas, start_date, end_date)
            if decision.include: decision = final_relevance_check(screener, paper, decision)
            decisions[paper.url] = decision
    elif not dry_run:
        raise RuntimeError("XAI_API_KEY is required for a non-dry run")
    included = [(paper, decision) for paper in papers if (decision := decisions.get(paper.url)) and decision.include]
    qc = run_qc(papers, decisions, search_executions, all_hits, duplicates, start_date, end_date)
    base = f"BNL_{start_date.isoformat()}_to_{end_date.isoformat()}"; report_path = run_dir / f"{base}{'_DRAFT' if not qc.passed else ''}.docx"
    write_docx(report_path, included, start_date, end_date, draft=not qc.passed)
    audit_path = run_dir / "audit_log.csv"; write_audit_csv(audit_path, all_hits, papers, decisions)
    qc_path = run_dir / "qc_report.txt"; qc_path.write_text(qc.text(), encoding="utf-8")
    (run_dir / "search_log.json").write_text(json.dumps([entry.asdict() for entry in search_executions], indent=2), encoding="utf-8")
    (run_dir / "screening_results.json").write_text(json.dumps({url: d.model_dump() for url, d in decisions.items()}, indent=2), encoding="utf-8")
    (run_dir / "dedupe_log.json").write_text(json.dumps(duplicates, indent=2), encoding="utf-8")
    write_papers_exports(run_dir / "papers.csv", run_dir / "papers.xlsx", included)
    return RunResult(run_dir, report_path, audit_path, qc_path, qc)
