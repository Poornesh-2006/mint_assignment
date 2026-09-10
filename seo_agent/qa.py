"""Grounded Q&A: retrieve an unaltered page passage, or return null."""

from __future__ import annotations

import math
import re
from typing import Any

from seo_agent.html_utils import parse_html, visible_text

STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "is",
    "are",
    "was",
    "were",
    "be",
    "by",
    "at",
    "as",
    "from",
    "that",
    "this",
    "it",
    "its",
    "your",
    "our",
    "you",
    "we",
    "do",
    "does",
    "what",
    "when",
    "where",
    "who",
    "how",
    "why",
    "which",
    "can",
    "could",
    "should",
    "would",
    "about",
    "please",
    "tell",
    "me",
}


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def query_terms(query: str) -> list[str]:
    return [t for t in tokenize(query) if t not in STOPWORDS and len(t) > 1]


TIME_RE = re.compile(
    r"\b(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:[ap]\.?m\.?)\b|\b24\s*(?:hours|hrs)\b",
    re.I,
)
HOUR_QUERY_TERMS = {"hour", "hours", "open", "opening", "opened", "close", "closed", "closing", "schedule"}
LOCATION_QUERY_TERMS = {"address", "located", "location", "where", "street", "directions"}


def looks_like_chrome(text: str) -> bool:
    """True for nav/menu/chrome that should never be returned as an answer."""
    words = text.split()
    if not words:
        return True
    if re.search(r"skip to (content|main)|cookies are disabled|login / register", text, re.I):
        return True
    cap = sum(1 for w in words if w[:1].isupper() and w.lower() not in {"am", "pm"})
    has_digit = bool(re.search(r"\d", text))
    if len(words) >= 6 and cap / len(words) >= 0.7 and not has_digit:
        return True
    if text.count(" & ") >= 2 and not has_digit:
        return True
    return False


def chunk_text(text: str, max_chars: int = 420) -> list[str]:
    """Helpora-style: split on blank lines / paragraphs, then hard-wrap long ones."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        collapsed = re.sub(r"\s+", " ", text).strip()
        paragraphs = [collapsed] if collapsed else []
    chunks: list[str] = []
    for para in paragraphs:
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        if len(para) <= max_chars:
            chunks.append(para)
            continue
        words, current = para.split(), ""
        for word in words:
            trial = (current + " " + word).strip()
            if len(trial) > max_chars and current:
                chunks.append(current.strip())
                current = word
            else:
                current = trial
        if current.strip():
            chunks.append(current.strip())
    return chunks


def passages_from_pages(pages: list[dict[str, Any]], max_chars: int = 420) -> list[dict[str, str]]:
    passages: list[dict[str, str]] = []
    idx = 0
    for page in pages:
        html = page.get("html") or ""
        status = int(page.get("status") or 0)
        if status != 200 or not html:
            continue
        soup = parse_html(html)
        for tag in soup(["script", "style", "noscript", "svg", "nav", "header", "footer", "form", "iframe"]):
            tag.decompose()
        for tag in soup.find_all(attrs={"role": re.compile(r"navigation|banner|contentinfo", re.I)}):
            tag.decompose()
        blocks = []
        root = soup.find("main") or soup.find("article") or soup
        for tag in root.find_all(["p", "li", "h1", "h2", "h3", "h4", "td", "address", "article", "div"]):
            if tag.name == "div" and not tag.get_text(" ", strip=True):
                continue
            if tag.name == "div" and len(tag.find_all("div")) > 3:
                continue
            text = tag.get_text(" ", strip=True)
            if text and len(text) >= 50 and not looks_like_chrome(text):
                blocks.append(text)
        if not blocks:
            text = visible_text(root)
            blocks = [c for c in chunk_text(text, max_chars=max_chars) if not looks_like_chrome(c)]
        else:
            rebuilt = []
            seen = set()
            for block in blocks:
                for chunk in chunk_text(block, max_chars=max_chars):
                    key = chunk.lower()
                    if key in seen or looks_like_chrome(chunk) or len(chunk) < 50:
                        continue
                    seen.add(key)
                    rebuilt.append(chunk)
            blocks = rebuilt
        url = page.get("final_url") or page.get("url") or ""
        for block in blocks:
            passages.append({"id": f"p-{idx}", "url": url, "text": block})
            idx += 1
    return passages


def _idf(df: int, n: int) -> float:
    return math.log((n - df + 0.5) / (df + 0.5) + 1.0)


def rank_passages(query: str, passages: list[dict[str, str]], top_k: int = 8) -> list[dict[str, Any]]:
    if not passages or not (query or "").strip():
        return []
    terms = query_terms(query)
    if not terms:
        terms = tokenize(query)
    n = len(passages)
    df: dict[str, int] = {}
    tokenized = []
    for p in passages:
        toks = tokenize(p["text"])
        tokenized.append(toks)
        for uniq in set(toks):
            df[uniq] = df.get(uniq, 0) + 1
    avgdl = sum(len(t) for t in tokenized) / max(1, n)
    k1, b = 1.5, 0.75
    scored = []
    for p, toks in zip(passages, tokenized):
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        hits = 0
        for term in terms:
            if term not in tf:
                continue
            hits += 1
            idf = _idf(df.get(term, 0), n)
            freq = tf[term]
            dl = len(toks) or 1
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * dl / avgdl))
        coverage = hits / max(1, len(set(terms)))
        qset = set(terms)
        if qset & HOUR_QUERY_TERMS and TIME_RE.search(p["text"]):
            score += 4.0
        if qset & LOCATION_QUERY_TERMS and re.search(r"\b\d{1,6}\s+[A-Za-z]", p["text"]):
            score += 2.5
        scored.append({**p, "score": score, "coverage": coverage})
    scored.sort(key=lambda x: (x["score"], x["coverage"], len(x["text"])), reverse=True)
    qset = set(terms)
    if qset & HOUR_QUERY_TERMS:
        timed = [s for s in scored if TIME_RE.search(s["text"])]
        scored = timed
    return scored[:top_k]


def chroma_rerank(query: str, passages: list[dict[str, str]], top_k: int = 8) -> list[dict[str, Any]] | None:
    if not passages:
        return None
    try:
        import chromadb
    except Exception:
        return None
    try:
        client = chromadb.Client()
        name = "seo_qa_passages"
        try:
            client.delete_collection(name)
        except Exception:
            pass
        collection = client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
        # Chroma has a max batch; keep it modest.
        ids = [p["id"] for p in passages]
        docs = [p["text"][:2000] for p in passages]
        metas = [{"url": p["url"]} for p in passages]
        batch = 100
        for i in range(0, len(ids), batch):
            collection.add(
                ids=ids[i : i + batch],
                documents=docs[i : i + batch],
                metadatas=metas[i : i + batch],
            )
        result = collection.query(query_texts=[query], n_results=min(top_k, len(passages)))
        hits = []
        docs_out = (result.get("documents") or [[]])[0]
        metas_out = (result.get("metadatas") or [[]])[0]
        ids_out = (result.get("ids") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        by_id = {p["id"]: p for p in passages}
        for i, pid in enumerate(ids_out):
            base = by_id.get(pid) or {
                "id": pid,
                "url": (metas_out[i] or {}).get("url", ""),
                "text": docs_out[i],
            }
            dist = dists[i] if i < len(dists) else 1.0
            hits.append({**base, "score": 1.0 - float(dist), "coverage": 0.0, "chroma": True})
        return hits
    except Exception:
        return None


def rrf_merge(lexical: list[dict[str, Any]], vector: list[dict[str, Any]] | None, k: int = 8) -> list[dict[str, Any]]:
    ranks: dict[str, float] = {}
    store: dict[str, dict[str, Any]] = {}
    for collection in (lexical, vector or []):
        for rank, item in enumerate(collection, start=1):
            pid = item["id"]
            store[pid] = item
            ranks[pid] = ranks.get(pid, 0.0) + 1.0 / (60 + rank)
    ordered = sorted(store.values(), key=lambda p: ranks.get(p["id"], 0.0), reverse=True)
    return ordered[:k]


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def verbatim_on_page(excerpt: str, html: str) -> bool:
    if not excerpt or not html:
        return False
    soup = parse_html(html)
    page_text = normalize_ws(visible_text(soup))
    return normalize_ws(excerpt) in page_text


def should_abstain(query: str, ranked: list[dict[str, Any]]) -> bool:
    if not ranked:
        return True
    top = ranked[0]
    text = top.get("text") or ""
    if looks_like_chrome(text):
        return True
    terms = set(query_terms(query))
    if terms & HOUR_QUERY_TERMS and not TIME_RE.search(text):
        return True
    if not terms:
        return top.get("score", 0) < 1.5
    if top.get("coverage", 0) <= 0 and top.get("score", 0) < 2.0 and not top.get("chroma"):
        return True
    if top.get("coverage", 0) == 0 and top.get("score", 0) < 0.35:
        return True
    return False


def null_answer(query: str) -> dict[str, Any]:
    return {"query": query, "url": None, "excerpt": None}


def grounded_answer(
    query: str,
    pages: list[dict[str, Any]],
    chosen: dict[str, str] | None,
) -> dict[str, Any]:
    """Return url+excerpt only when the excerpt is an unaltered substring of the page."""
    if not query.strip():
        return null_answer(query)
    if not chosen or not chosen.get("text") or not chosen.get("url"):
        return null_answer(query)
    excerpt = chosen["text"]
    url = chosen["url"]
    page = next((p for p in pages if (p.get("final_url") or p.get("url")) == url), None)
    if not page or not verbatim_on_page(excerpt, page.get("html") or ""):
        return null_answer(query)
    return {"query": query, "url": url, "excerpt": excerpt}
