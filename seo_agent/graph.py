"""LangGraph pipeline: crawl → on-page audit → NAP → grounded Q&A → write JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from seo_agent.audit import audit_pages
from seo_agent.config import CITATION_HOSTS, DEFAULT_MAX_PAGES, DEFAULT_OUT_DIR
from seo_agent.crawler import crawl_site, fetch_extra, outbound_citation_urls
from seo_agent.html_utils import parse_html, visible_text
from seo_agent.llm import (
    NAP_PROMPT,
    QA_PROMPT,
    complete,
    groq_available,
    invoke_agent,
    make_nap_agent,
    make_qa_agent,
    parse_json_object,
)
from seo_agent.nap import compare_nap, extract_page_nap, merge_llm_extraction, spread_business_name
from seo_agent.tools import search_site, set_passages
from seo_agent.qa import (
    chroma_rerank,
    grounded_answer,
    null_answer,
    passages_from_pages,
    rank_passages,
    rrf_merge,
    should_abstain,
)


class State(TypedDict, total=False):
    url: str
    query: str
    max_pages: int
    out_dir: str
    crawl: dict[str, Any]
    audit: list[dict[str, Any]]
    nap_extractions: list[dict[str, Any]]
    nap_report: list[dict[str, Any]]
    answer: dict[str, Any]
    written: dict[str, str]


def crawl_node(state: State) -> dict[str, Any]:
    crawl = crawl_site(state["url"], max_pages=int(state.get("max_pages") or DEFAULT_MAX_PAGES))
    extra_urls = outbound_citation_urls(crawl["pages"], crawl["start_url"], CITATION_HOSTS)
    if extra_urls:
        crawl["citation_pages"] = fetch_extra(extra_urls)
    else:
        crawl["citation_pages"] = []
    return {"crawl": crawl}


def audit_node(state: State) -> dict[str, Any]:
    return {"audit": audit_pages(state["crawl"])}


def nap_node(state: State) -> dict[str, Any]:
    crawl = state["crawl"]
    pages = list(crawl.get("pages") or []) + list(crawl.get("citation_pages") or [])
    live = [p for p in pages if int(p.get("status") or 0) == 200 and p.get("html")]
    extractions = [extract_page_nap(p) for p in live]

    prioritized = []
    for page in live:
        url = (page.get("final_url") or page.get("url") or "").lower()
        if page is live[0] or any(tok in url for tok in ("contact", "about", "location", "find", "address", "hour")):
            prioritized.append(page)
    if not prioritized and live:
        prioritized = live[:2]

    if groq_available():
        agent = None
        try:
            agent = make_nap_agent()
        except Exception:
            agent = None
        for page in prioritized[:4]:
            soup = parse_html(page.get("html") or "")
            text = visible_text(soup)[:3500]
            user = f"PAGE URL: {page.get('final_url') or page.get('url')}\nPAGE TEXT:\n{text}"
            raw = invoke_agent(agent, user) if agent is not None else ""
            if not raw:
                raw = complete(NAP_PROMPT, user)
            payload = parse_json_object(raw)
            if payload:
                target = next(
                    (
                        e
                        for e in extractions
                        if e["url"] == (page.get("final_url") or page.get("url"))
                    ),
                    None,
                )
                if target:
                    merge_llm_extraction(target, payload, text)

    spread_business_name(extractions, live)
    report = compare_nap(extractions)
    return {"nap_extractions": extractions, "nap_report": report}


def qa_node(state: State) -> dict[str, Any]:
    query = (state.get("query") or "").strip()
    pages = [p for p in (state["crawl"].get("pages") or []) if int(p.get("status") or 0) == 200]
    if not query:
        return {"answer": null_answer(query)}

    passages = passages_from_pages(pages)
    set_passages(passages)
    search_site.invoke(query)
    lexical = rank_passages(query, passages, top_k=12)
    vector = chroma_rerank(query, passages, top_k=8)
    ranked = rrf_merge(lexical, vector, k=8)

    if should_abstain(query, ranked):
        return {"answer": null_answer(query)}

    chosen = ranked[0]
    if groq_available():
        numbered = "\n\n".join(
            f"[{i}] URL: {p['url']}\nPASSAGE: {p['text']}" for i, p in enumerate(ranked)
        )
        user = f"QUERY: {query}\n\nPASSAGES:\n{numbered}"
        raw = ""
        try:
            agent = make_qa_agent()
            if agent is not None:
                raw = invoke_agent(agent, user)
        except Exception:
            raw = ""
        if not raw:
            raw = complete(QA_PROMPT, user)
        payload = parse_json_object(raw)
        if payload and "index" in payload:
            index = payload.get("index")
            if index is None:
                if chosen.get("coverage", 0) < 0.45:
                    return {"answer": null_answer(query)}
            else:
                try:
                    index = int(index)
                except (TypeError, ValueError):
                    index = None
                if index is not None and 0 <= index < len(ranked):
                    chosen = ranked[index]

    return {"answer": grounded_answer(query, pages, chosen)}


def write_node(state: State) -> dict[str, Any]:
    out_dir = Path(state.get("out_dir") or DEFAULT_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_path = out_dir / "audit.json"
    nap_path = out_dir / "nap_report.json"
    answer_path = out_dir / "answer.json"
    meta_path = out_dir / "run_meta.json"

    dump = lambda obj: json.dumps(obj, indent=2, ensure_ascii=False)
    audit_path.write_text(dump(state.get("audit") or []), encoding="utf-8")
    nap_path.write_text(dump(state.get("nap_report") or []), encoding="utf-8")
    answer_path.write_text(dump(state.get("answer") or null_answer("")), encoding="utf-8")
    meta = {
        "input_url": state.get("url"),
        "query": state.get("query"),
        "start_url": (state.get("crawl") or {}).get("start_url"),
        "pages_crawled": (state.get("crawl") or {}).get("page_count"),
        "groq_used": groq_available(),
        "audit_findings": len(state.get("audit") or []),
        "nap_fields": len(state.get("nap_report") or []),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out_dir / "demo_url.txt").write_text(state.get("url") or "", encoding="utf-8")
    if state.get("query"):
        (out_dir / "query.txt").write_text(state["query"], encoding="utf-8")
    return {
        "written": {
            "audit": str(audit_path),
            "nap_report": str(nap_path),
            "answer": str(answer_path),
            "run_meta": str(meta_path),
        }
    }


def build_graph():
    builder = StateGraph(State)
    builder.add_node("crawl", crawl_node)
    builder.add_node("audit", audit_node)
    builder.add_node("nap", nap_node)
    builder.add_node("qa", qa_node)
    builder.add_node("write", write_node)
    builder.add_edge(START, "crawl")
    builder.add_edge("crawl", "audit")
    builder.add_edge("audit", "nap")
    builder.add_edge("nap", "qa")
    builder.add_edge("qa", "write")
    builder.add_edge("write", END)
    return builder.compile()


graph = build_graph()


def run_audit_agent(
    url: str,
    query: str = "",
    max_pages: int = DEFAULT_MAX_PAGES,
    out_dir: str | Path | None = None,
) -> State:
    return graph.invoke(
        {
            "url": url,
            "query": query,
            "max_pages": max_pages,
            "out_dir": str(out_dir or DEFAULT_OUT_DIR),
        }
    )
