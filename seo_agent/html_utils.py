"""HTML / URL helpers used by the crawler, auditor, and NAP extractor."""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

SKIP_SCHEMES = {"mailto", "tel", "javascript", "data", "sms", "whatsapp"}
SKIP_EXT = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".avif",
    ".zip",
    ".mp4",
    ".mp3",
    ".css",
    ".js",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".xml",
    ".json",
    ".csv",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
}


def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


def normalize_url(url: str, base: str | None = None) -> str:
    if base:
        url = urljoin(base, url)
    url, _frag = urldefrag(url.strip())
    parsed = urlparse(url)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def registrable_host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def same_site(a: str, b: str) -> bool:
    return registrable_host(a) == registrable_host(b)


def is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_crawlable_href(href: str) -> bool:
    if not href or href.startswith("#"):
        return False
    parsed = urlparse(href)
    if parsed.scheme and parsed.scheme.lower() in SKIP_SCHEMES:
        return False
    path = (parsed.path or "").lower()
    return not any(path.endswith(ext) for ext in SKIP_EXT)


def meta_content(soup: BeautifulSoup, **attrs: str) -> str | None:
    tag = soup.find("meta", attrs=attrs)
    if not tag:
        return None
    content = tag.get("content")
    if content is None:
        return None
    text = unescape(str(content)).strip()
    return text or None


def attr_or_none(tag: Tag | None, name: str) -> str | None:
    if not tag:
        return None
    value = tag.get(name)
    if value is None:
        return None
    text = unescape(str(value)).strip()
    return text or None


def visible_text(soup: BeautifulSoup) -> str:
    clone = parse_html(str(soup))
    for tag in clone(["script", "style", "noscript", "svg", "iframe", "canvas"]):
        tag.decompose()
    return re.sub(r"\s+", " ", clone.get_text(" ")).strip()


def heading_outline(soup: BeautifulSoup) -> list[dict[str, Any]]:
    outline = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        outline.append(
            {
                "tag": tag.name,
                "level": int(tag.name[1]),
                "text": tag.get_text(" ", strip=True)[:200],
            }
        )
    return outline


def json_ld_blocks(soup: BeautifulSoup) -> list[Any]:
    blocks: list[Any] = []
    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").lower()
        if "ld+json" not in script_type:
            continue
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                data = json.loads(re.sub(r",\s*}", "}", re.sub(r",\s*]", "]", raw)))
            except json.JSONDecodeError:
                continue
        if isinstance(data, list):
            blocks.extend(data)
        elif isinstance(data, dict) and "@graph" in data:
            graph = data.get("@graph")
            if isinstance(graph, list):
                blocks.extend(graph)
            else:
                blocks.append(data)
        else:
            blocks.append(data)
    return blocks


def flatten_schema_types(block: Any) -> list[str]:
    if not isinstance(block, dict):
        return []
    raw = block.get("@type")
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if raw:
        return [str(raw)]
    return []


def excerpt_markup(tag: Tag | None, limit: int = 240) -> str:
    if tag is None:
        return ""
    html = re.sub(r"\s+", " ", str(tag))
    return html[:limit] + ("…" if len(html) > limit else "")
