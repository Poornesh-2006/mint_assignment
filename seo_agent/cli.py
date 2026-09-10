"""CLI: python -m seo_agent --url https://example.com --query "What are your hours?" """

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from seo_agent.config import DEFAULT_MAX_PAGES, DEFAULT_OUT_DIR
from seo_agent.graph import run_audit_agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Crawl a URL and emit audit.json, nap_report.json, and answer.json."
    )
    parser.add_argument("--url", required=True, help="Site or business URL to crawl")
    parser.add_argument(
        "--query",
        default="",
        help="Natural-language question for the grounded Q&A agent (Question 3)",
    )
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args(argv)

    result = run_audit_agent(
        url=args.url,
        query=args.query,
        max_pages=args.max_pages,
        out_dir=Path(args.out_dir),
    )
    written = result.get("written") or {}
    summary = {
        "input_url": args.url,
        "query": args.query or None,
        "pages_crawled": (result.get("crawl") or {}).get("page_count"),
        "audit_findings": len(result.get("audit") or []),
        "nap_fields": len(result.get("nap_report") or []),
        "answer": result.get("answer"),
        "files": written,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
