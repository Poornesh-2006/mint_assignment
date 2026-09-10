"""Same-origin crawler: robots.txt, sitemap, BFS links, common local-SEO paths."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from collections import deque
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from seo_agent.config import (
    COMMON_PATHS,
    DEFAULT_MAX_PAGES,
    FETCH_DELAY_SEC,
    MAX_HTML_BYTES,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from seo_agent.html_utils import (
    is_crawlable_href,
    is_http_url,
    normalize_url,
    parse_html,
    registrable_host,
    same_site,
)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
)


def _fetch(url: str, *, stream_html: bool = True) -> dict[str, Any]:
    page: dict[str, Any] = {
        "url": url,
        "final_url": url,
        "status": 0,
        "content_type": "",
        "html": "",
        "headers": {},
        "error": None,
        "redirect_chain": [],
        "from_sitemap": False,
        "from_probe": False,
    }
    try:
        resp = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        page["status"] = resp.status_code
        page["final_url"] = str(resp.url)
        page["content_type"] = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        page["headers"] = {
            k.lower(): v
            for k, v in resp.headers.items()
            if k.lower() in {"content-type", "x-robots-tag", "location"}
        }
        page["redirect_chain"] = [str(h.url) for h in resp.history]
        content_type = page["content_type"]
        if stream_html and "html" not in content_type and "xml" not in content_type:
            resp.close()
            page["error"] = f"skipped non-html content-type {content_type or 'unknown'}"
            return page
        raw = resp.content[: MAX_HTML_BYTES + 1]
        resp.close()
        if len(raw) > MAX_HTML_BYTES:
            raw = raw[:MAX_HTML_BYTES]
            page["error"] = "html truncated at 1.5MB"
        page["html"] = raw.decode(resp.encoding or "utf-8", errors="replace")
    except requests.RequestException as exc:
        page["error"] = str(exc)
    return page


def _robots(start_url: str) -> RobotFileParser:
    parsed = urlparse(start_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        resp = SESSION.get(robots_url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200 and resp.text:
            rp.parse(resp.text.splitlines())
        else:
            rp.parse([])
        rp.robots_body = resp.text if resp.status_code == 200 else ""
        rp.robots_status = resp.status_code
        rp.robots_url = robots_url
        rp.sitemap_hints = [
            line.split(":", 1)[1].strip()
            for line in (resp.text or "").splitlines()
            if line.lower().startswith("sitemap:")
        ]
    except requests.RequestException:
        rp.parse([])
        rp.robots_body = ""
        rp.robots_status = 0
        rp.robots_url = robots_url
        rp.sitemap_hints = []
    return rp


def _local_tag(tag: str) -> str:
    return tag.split("}")[-1].lower()


def _parse_sitemap(xml_text: str, base_url: str) -> tuple[list[str], list[str]]:
    urls: list[str] = []
    nested: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return urls, nested

    def loc_text(el: ET.Element) -> str | None:
        for child in list(el) + [el]:
            if _local_tag(child.tag) == "loc" and child.text:
                try:
                    return normalize_url(child.text.strip(), base_url)
                except Exception:
                    return None
        return None

    if _local_tag(root.tag) == "sitemapindex":
        for sm in root:
            if _local_tag(sm.tag) == "sitemap":
                loc = loc_text(sm)
                if loc:
                    nested.append(loc)
        return urls, nested

    for url_el in root:
        if _local_tag(url_el.tag) == "url":
            loc = loc_text(url_el)
            if loc:
                urls.append(loc)
    if not urls:
        for el in root.iter():
            if _local_tag(el.tag) == "loc" and el.text:
                try:
                    urls.append(normalize_url(el.text.strip(), base_url))
                except Exception:
                    continue
    return urls, nested


def _sitemap_urls(start_url: str, rp: RobotFileParser, limit: int) -> list[str]:
    parsed = urlparse(start_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates = list(getattr(rp, "sitemap_hints", []) or [])
    candidates.extend(
        [
            urljoin(origin, "/sitemap.xml"),
            urljoin(origin, "/sitemap_index.xml"),
            urljoin(origin, "/sitemap-index.xml"),
        ]
    )
    found: list[str] = []
    seen_maps: set[str] = set()
    queue = []
    for c in candidates:
        try:
            queue.append(normalize_url(c, origin))
        except Exception:
            continue
    while queue and len(found) < limit:
        sm_url = queue.pop(0)
        if sm_url in seen_maps:
            continue
        seen_maps.add(sm_url)
        page = _fetch(sm_url, stream_html=False)
        time.sleep(FETCH_DELAY_SEC)
        if page["status"] != 200 or not page["html"]:
            continue
        pages, nested = _parse_sitemap(page["html"], origin)
        for n in nested:
            if n not in seen_maps:
                queue.append(n)
        for u in pages:
            if same_site(u, start_url) and u not in found:
                found.append(u)
            if len(found) >= limit:
                break
    return found


def _extract_links(html: str, base_url: str) -> list[str]:
    soup = parse_html(html)
    links: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = str(tag.get("href") or "").strip()
        if not is_crawlable_href(href):
            continue
        try:
            abs_url = normalize_url(href, base_url)
        except Exception:
            continue
        if not is_http_url(abs_url) or abs_url in seen:
            continue
        seen.add(abs_url)
        links.append(abs_url)
    return links


LOCAL_PATH_HINTS = (
    "contact",
    "about",
    "hour",
    "location",
    "address",
    "visit",
    "find",
    "story",
    "history",
    "faq",
    "privacy",
    "menu",
)


def _local_seo_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(h in path for h in LOCAL_PATH_HINTS)


def crawl_site(start_url: str, max_pages: int = DEFAULT_MAX_PAGES) -> dict[str, Any]:
    """BFS crawl of one registrable domain. Returns pages plus crawl metadata."""
    seed = normalize_url(start_url)
    origin_host = registrable_host(seed)
    rp = _robots(seed)

    def allowed(url: str) -> bool:
        return rp.can_fetch(USER_AGENT, url) or rp.can_fetch("*", url)

    primary: deque[str] = deque()
    secondary: deque[str] = deque()
    seen_request: set[str] = set()
    seen_final: set[str] = set()
    pages: list[dict[str, Any]] = []
    flags: dict[str, dict[str, bool]] = {}
    sitemap_urls = _sitemap_urls(seed, rp, limit=max_pages)

    def enqueue(
        url: str,
        *,
        from_sitemap: bool = False,
        from_probe: bool = False,
        late: bool = False,
        front: bool = False,
    ) -> None:
        try:
            url = normalize_url(url)
        except Exception:
            return
        if url in seen_request or not same_site(url, seed):
            return
        seen_request.add(url)
        flags[url] = {"from_sitemap": from_sitemap, "from_probe": from_probe}
        target = secondary if late or from_probe else primary
        if front:
            target.appendleft(url)
        else:
            target.append(url)

    enqueue(seed)
    while (primary or secondary) and len(pages) < max_pages:
        url = primary.popleft() if primary else secondary.popleft()
        if not allowed(url):
            continue
        page = _fetch(url)
        time.sleep(FETCH_DELAY_SEC)
        page_flags = flags.get(url, {})
        page["from_sitemap"] = bool(page_flags.get("from_sitemap"))
        page["from_probe"] = bool(page_flags.get("from_probe"))
        final = page.get("final_url") or url
        try:
            final_key = normalize_url(final)
        except Exception:
            final_key = final
        if final_key in seen_final:
            continue
        # Failed common-path probes are not site issues and must not eat the budget.
        if page["from_probe"] and int(page.get("status") or 0) >= 400:
            continue
        seen_final.add(final_key)
        pages.append(page)

        if len(pages) == 1:
            parsed = urlparse(page.get("final_url") or seed)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            for link in _extract_links(page.get("html") or "", page.get("final_url") or url):
                enqueue(link, front=_local_seo_url(link))
            for sm_url in sitemap_urls:
                enqueue(sm_url, from_sitemap=True)
            for path in COMMON_PATHS:
                enqueue(urljoin(origin, path), from_probe=True, late=True)
            continue

        if page.get("html") and int(page.get("status") or 0) == 200:
            for link in _extract_links(page["html"], page["final_url"] or url):
                enqueue(link, front=_local_seo_url(link))

    robots_status = getattr(rp, "robots_status", 0)
    return {
        "start_url": seed,
        "host": origin_host,
        "robots_url": getattr(rp, "robots_url", ""),
        "robots_status": robots_status,
        "robots_body": getattr(rp, "robots_body", "")[:4000],
        "sitemap_urls": sitemap_urls[:50],
        "page_count": len(pages),
        "pages": pages,
    }


def outbound_citation_urls(pages: list[dict[str, Any]], start_url: str, hosts: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for page in pages:
        html = page.get("html") or ""
        if not html:
            continue
        for link in _extract_links(html, page.get("final_url") or page["url"]):
            low = link.lower()
            if any(h in low for h in hosts) and link not in seen:
                seen.add(link)
                found.append(link)
    return found[:8]


def fetch_extra(urls: list[str]) -> list[dict[str, Any]]:
    extra = []
    for url in urls:
        page = _fetch(url)
        page["from_citation"] = True
        extra.append(page)
        time.sleep(FETCH_DELAY_SEC)
    return extra
