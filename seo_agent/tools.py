"""LangChain tools — Helpora-style retrieval over the crawled site."""

from __future__ import annotations

from langchain_core.tools import tool

from seo_agent.qa import rank_passages

_PASSAGES: list[dict[str, str]] = []


def set_passages(passages: list[dict[str, str]]) -> None:
    global _PASSAGES
    _PASSAGES = list(passages)


@tool
def search_site(question: str) -> str:
    """Search crawled pages for the passage relevant to a question."""
    hits = rank_passages(question, _PASSAGES, top_k=4)
    if not hits:
        return "No passages indexed."
    return "\n---\n".join(f"URL: {h['url']}\n{h['text']}" for h in hits)


@tool
def list_crawled_hosts() -> str:
    """List distinct page URLs currently in the passage index."""
    urls = sorted({p["url"] for p in _PASSAGES})
    return "\n".join(urls[:40]) or "No pages indexed."
