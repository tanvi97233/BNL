#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from bnl_tool.config import ROOT, Settings, resolve_date_range
from bnl_tool.pipeline import run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the BNL SOP literature workflow.")
    parser.add_argument("--start-date", help="YYYY-MM-DD; overrides START_DATE")
    parser.add_argument("--end-date", help="YYYY-MM-DD; overrides END_DATE")
    parser.add_argument("--dry-run", action="store_true", help="Run two strings and one Google page each.")
    parser.add_argument("--resume", action="store_true", help="Reuse cached paper pages.")
    parser.add_argument("--max-pages", type=int, default=5, help="Maximum Google result pages per query (default: 5).")
    parser.add_argument("--only-string", type=int, help="Run only one 1-based source search-string number.")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    try:
        start, end = resolve_date_range(args.start_date if args.start_date is not None else os.getenv("START_DATE"), args.end_date if args.end_date is not None else os.getenv("END_DATE"))
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Confirmed date range: {start.isoformat()} to {end.isoformat()}")
    settings = Settings.from_env()
    if not settings.xai_api_key and not args.dry_run: parser.error("XAI_API_KEY is required (a dry run may omit it).")
    if args.max_pages < 1: parser.error("--max-pages must be positive")
    result = run_pipeline(settings, start, end, max_pages=args.max_pages, dry_run=args.dry_run, resume=args.resume, only_string=args.only_string)
    print(f"QC: {'PASS' if result.qc.passed else 'FAIL — report marked DRAFT'}")
    print(f"Report: {result.report_path}")
    return 0 if result.qc.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
