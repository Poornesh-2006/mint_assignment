"""NAP consistency: extract name / address / phone from crawled pages, then compare."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from urllib.parse import urlparse

from seo_agent.html_utils import flatten_schema_types, json_ld_blocks, parse_html, visible_text

PHONE_RE = re.compile(
    r"""
    (?:(?:\+|00)\s?1[\s.\-]?)?
    (?:\(?\d{3}\)?[\s.\-]?)
    \d{3}[\s.\-]?\d{4}
    """,
    re.VERBOSE,
)

US_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'\-]+(?:\s+[A-Za-z0-9.'\-]+){0,5}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|"
    r"Way|Court|Ct|Place|Pl|Highway|Hwy|Parkway|Pkwy|Circle|Cir)\b"
    r"(?:[,\s]+(?:Suite|Ste|Unit|#)\s?[A-Za-z0-9\-]+)?",
    re.I,
)

CITY_STATE_ZIP_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,2}),\s*"
    r"([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\b"
)

CITY_ZIP_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,3}),\s+(\d{5}(?:-\d{4})?)\b"
)

LEGAL_SUFFIX_RE = re.compile(
    r"\b(incorporated|inc|llc|ltd|limited|co|corp|company|plc|pllc)\.?$",
    re.I,
)

STREET_CANON = {
    "street": "st",
    "st": "st",
    "avenue": "ave",
    "ave": "ave",
    "road": "rd",
    "rd": "rd",
    "boulevard": "blvd",
    "blvd": "blvd",
    "drive": "dr",
    "dr": "dr",
    "lane": "ln",
    "ln": "ln",
    "court": "ct",
    "ct": "ct",
    "place": "pl",
    "pl": "pl",
    "highway": "hwy",
    "hwy": "hwy",
    "parkway": "pkwy",
    "pkwy": "pkwy",
    "suite": "ste",
    "ste": "ste",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_name(raw: str) -> str:
    text = (raw or "").lower()
    text = re.sub(r"[®™©]", "", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = LEGAL_SUFFIX_RE.sub("", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return text


def normalize_address(raw: str) -> str:
    text = (raw or "").lower()
    text = text.replace(".", "")
    text = re.sub(r"[,#]", " ", text)
    parts = []
    for tok in re.split(r"\s+", text):
        if not tok:
            continue
        parts.append(STREET_CANON.get(tok, tok))
    return " ".join(parts)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _schema_text(value: Any) -> str:
    if isinstance(value, dict):
        if "name" in value:
            return str(value.get("name") or "")
        if "@value" in value:
            return str(value.get("@value") or "")
        return ""
    return str(value or "").strip()


def _walk_schema_nap(block: Any, acc: dict[str, list[str]]) -> None:
    if isinstance(block, list):
        for item in block:
            _walk_schema_nap(item, acc)
        return
    if not isinstance(block, dict):
        return
    types = {t.lower() for t in flatten_schema_types(block)}
    interesting = bool(
        types
        & {
            "localbusiness",
            "organization",
            "restaurant",
            "store",
            "foodestablishment",
            "place",
            "postaladdress",
            "dentist",
            "attorney",
            "hotel",
            "motel",
        }
        or "telephone" in block
        or "address" in block
    )
    if interesting:
        name = _schema_text(block.get("name"))
        if name:
            acc["business_name"].append(name)
        phone = block.get("telephone") or block.get("phone")
        for p in _as_list(phone):
            text = _schema_text(p)
            if text:
                acc["phone"].append(text)
        addr = block.get("address")
        for a in _as_list(addr):
            if isinstance(a, dict):
                street = _schema_text(a.get("streetAddress"))
                city = _schema_text(a.get("addressLocality"))
                region = _schema_text(a.get("addressRegion"))
                postal = _schema_text(a.get("postalCode"))
                country = _schema_text(a.get("addressCountry"))
                if street:
                    acc["street_address"].append(street)
                if city:
                    acc["city"].append(city)
                if region:
                    acc["region"].append(str(region))
                if postal:
                    acc["postal_code"].append(str(postal))
                pieces = [p for p in [street, city, region, postal, country] if p]
                if pieces:
                    acc["full_address"].append(", ".join(pieces))
            else:
                text = _schema_text(a)
                if text:
                    acc["full_address"].append(text)
    for key, val in block.items():
        if key.startswith("@"):
            continue
        if isinstance(val, (dict, list)):
            _walk_schema_nap(val, acc)


def extract_page_nap(page: dict[str, Any]) -> dict[str, Any]:
    url = page.get("final_url") or page.get("url") or ""
    html = page.get("html") or ""
    acc: dict[str, list[str]] = defaultdict(list)
    sources: dict[str, set[str]] = defaultdict(set)
    if not html:
        return {"url": url, "fields": {}, "sources": {}, "status": page.get("status")}

    soup = parse_html(html)
    for block in json_ld_blocks(soup):
        before = {k: list(v) for k, v in acc.items()}
        _walk_schema_nap(block, acc)
        for key, values in acc.items():
            if len(values) > len(before.get(key, [])):
                sources[key].add("json_ld")

    for tag in soup.find_all(itemprop=True):
        prop = (tag.get("itemprop") or "").lower()
        content = (tag.get("content") or tag.get_text(" ", strip=True) or "").strip()
        if not content:
            continue
        if prop in {"name"}:
            scope = tag.find_parent(attrs={"itemtype": True}) or tag
            itemtype = " ".join(scope.get("itemtype") or [] if isinstance(scope.get("itemtype"), list) else [str(scope.get("itemtype") or "")]).lower()
            if any(
                t in itemtype
                for t in ("organization", "localbusiness", "restaurant", "foodestablishment", "store")
            ):
                acc["business_name"].append(content)
                sources["business_name"].add("microdata")
        elif prop in {"telephone", "phone"}:
            acc["phone"].append(content)
            sources["phone"].add("microdata")
        elif prop == "streetaddress":
            acc["street_address"].append(content)
            sources["street_address"].add("microdata")
        elif prop == "addresslocality":
            acc["city"].append(content)
            sources["city"].add("microdata")
        elif prop == "addressregion":
            acc["region"].append(content)
            sources["region"].add("microdata")
        elif prop == "postalcode":
            acc["postal_code"].append(content)
            sources["postal_code"].add("microdata")

    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if href.lower().startswith("tel:"):
            number = href.split(":", 1)[1]
            acc["phone"].append(number)
            sources["phone"].add("tel_link")
            label = a.get_text(" ", strip=True)
            if label:
                acc["phone"].append(label)
                sources["phone"].add("tel_link")

    text = visible_text(soup)
    footer = soup.find("footer")
    header = soup.find("header")
    scoped = " ".join(
        [
            footer.get_text(" ", strip=True) if footer else "",
            header.get_text(" ", strip=True) if header else "",
            text[:2500],
        ]
    )
    for match in PHONE_RE.findall(scoped):
        acc["phone"].append(match.strip())
        sources["phone"].add("regex")
    for match in US_STREET_RE.finditer(scoped):
        acc["street_address"].append(re.sub(r"\s+", " ", match.group(0)).strip())
        sources["street_address"].add("regex")
    for match in CITY_STATE_ZIP_RE.finditer(scoped):
        acc["city"].append(match.group(1))
        acc["region"].append(match.group(2))
        acc["postal_code"].append(match.group(3))
        sources["city"].add("regex")
        sources["region"].add("regex")
        sources["postal_code"].add("regex")
    for match in CITY_ZIP_RE.finditer(scoped):
        acc["city"].append(match.group(1))
        acc["postal_code"].append(match.group(2))
        sources["city"].add("regex")
        sources["postal_code"].add("regex")

    path = urlparse(url).path or "/"
    if path in {"", "/"}:
        title_tag = soup.find("title")
        if title_tag:
            title_name = title_tag.get_text(" ", strip=True).split(" - ")[0].strip()
            if 2 <= len(title_name) <= 90 and "404" not in title_name.lower():
                acc["business_name"].append(title_name)
                sources["business_name"].add("homepage_title")

    og_site = None
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in {"og:site_name", "application-name"}:
            og_site = (meta.get("content") or "").strip()
            break
    if og_site:
        acc["business_name"].append(og_site)
        sources["business_name"].add("og_site_name")

    streets = acc.get("street_address") or []
    cities = acc.get("city") or []
    regions = acc.get("region") or []
    zips = acc.get("postal_code") or []
    if streets and (cities or zips) and not acc.get("full_address"):
        piece = ", ".join(
            p
            for p in [
                streets[0],
                cities[0] if cities else "",
                " ".join(x for x in [regions[0] if regions else "", zips[0] if zips else ""] if x),
            ]
            if p
        )
        acc["full_address"].append(piece)
        sources["full_address"].add("composed")

    fields = {}
    for key, values in acc.items():
        cleaned = []
        seen = set()
        for v in values:
            v = re.sub(r"\s+", " ", str(v)).strip()
            if not v:
                continue
            marker = v.lower()
            if marker in seen:
                continue
            seen.add(marker)
            cleaned.append(v)
        if cleaned:
            if key == "phone":
                cleaned = [v for v in cleaned if len(normalize_phone(v)) >= 10]
            elif key == "business_name":
                cleaned = [v for v in cleaned if 2 <= len(v) <= 90]
            if cleaned:
                fields[key] = cleaned
    return {
        "url": url,
        "fields": fields,
        "sources": {k: sorted(v) for k, v in sources.items()},
        "status": page.get("status"),
        "from_citation": bool(page.get("from_citation")),
    }


def merge_llm_extraction(page_nap: dict[str, Any], payload: dict[str, Any], page_text: str) -> dict[str, Any]:
    """Accept LLM NAP only when the claimed value actually appears on the page."""
    mapping = {
        "name": "business_name",
        "business_name": "business_name",
        "phone": "phone",
        "telephone": "phone",
        "address": "full_address",
        "full_address": "full_address",
        "street_address": "street_address",
        "city": "city",
        "region": "region",
        "state": "region",
        "postal_code": "postal_code",
        "zip": "postal_code",
    }
    blob = page_text.lower()
    fields = page_nap.setdefault("fields", {})
    sources = page_nap.setdefault("sources", {})
    for raw_key, value in (payload or {}).items():
        field = mapping.get(str(raw_key).lower())
        if not field or not value:
            continue
        text = str(value).strip()
        if not text:
            continue
        if field == "phone":
            digits = normalize_phone(text)
            page_digits = re.sub(r"\D", "", page_text)
            if digits and digits not in page_digits:
                continue
        else:
            tokens = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2]
            if tokens and sum(1 for t in tokens if t in blob) < max(1, len(tokens) // 2):
                continue
        values = fields.setdefault(field, [])
        if text.lower() not in {v.lower() for v in values}:
            values.append(text)
        src = sources.setdefault(field, [])
        if "llm" not in src:
            src.append("llm")
    return page_nap


def _confidence(pages_with_field: int, unique_norm: int, sources: set[str], formatting_only: bool) -> float:
    base = 0.35
    base += min(pages_with_field, 6) * 0.08
    if "json_ld" in sources:
        base += 0.18
    elif "microdata" in sources or "tel_link" in sources:
        base += 0.1
    elif "llm" in sources:
        base += 0.04
    if unique_norm <= 1:
        base += 0.15
    else:
        base -= min(0.35, 0.12 * (unique_norm - 1))
    if formatting_only:
        base += 0.08
    return round(min(0.97, max(0.12, base)), 2)


def compare_nap(extractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    field_order = [
        "business_name",
        "phone",
        "street_address",
        "city",
        "region",
        "postal_code",
        "full_address",
    ]
    reports = []
    for field in field_order:
        pages_compared = []
        values = []
        normalized_values = []
        sources: set[str] = set()
        per_page_norm: dict[str, set[str]] = {}

        for item in extractions:
            raws = (item.get("fields") or {}).get(field) or []
            if not raws:
                continue
            url = item["url"]
            if url not in pages_compared:
                pages_compared.append(url)
            values.extend(raws)
            norms = set()
            for raw in raws:
                if field == "phone":
                    n = normalize_phone(raw)
                    if len(n) < 7:
                        continue
                elif field == "business_name":
                    n = normalize_name(raw)
                else:
                    n = normalize_address(raw)
                if n:
                    norms.add(n)
                    normalized_values.append(n)
            if norms:
                per_page_norm[url] = norms
            sources.update(item.get("sources", {}).get(field) or [])

        unique_raw = list(dict.fromkeys(values))
        unique_norm = list(dict.fromkeys(normalized_values))
        page_count = len(pages_compared)

        if field == "business_name" and per_page_norm:
            counts: dict[str, int] = defaultdict(int)
            for norms in per_page_norm.values():
                for n in norms:
                    counts[n] += 1
            if counts:
                top_name, top_n = max(counts.items(), key=lambda kv: kv[1])
                if top_n >= 2 and top_n / max(1, len(per_page_norm)) >= 0.6:
                    unique_norm = [top_name]
                    unique_raw = [
                        v
                        for v in unique_raw
                        if normalize_name(v) == top_name
                    ]
                    per_page_norm = {
                        url: {n for n in norms if n == top_name}
                        for url, norms in per_page_norm.items()
                        if top_name in norms
                    }
                    pages_compared = [u for u in pages_compared if u in per_page_norm]
                    page_count = len(pages_compared)

        if page_count == 0:
            verdict = "insufficient_evidence"
            explanation = f"No {field} values were extracted from crawled pages."
        elif page_count == 1:
            verdict = "insufficient_evidence"
            explanation = (
                f"Only 1 page yielded a {field} value, so cross-page consistency cannot be judged. "
                f"Observed: {unique_raw[:3]!r}."
            )
        elif len(unique_norm) <= 1:
            if len(unique_raw) <= 1:
                verdict = "consistent"
                explanation = (
                    f"{field} matches across {page_count} pages. "
                    f"Raw values agree exactly: {unique_raw[:4]!r}."
                )
            else:
                verdict = "consistent_formatting_only"
                explanation = (
                    f"{field} raw strings differ ({unique_raw[:6]!r}) but collapse to one "
                    f"normalized value {unique_norm[:1]!r}. Treated as formatting, not a NAP mismatch."
                )
        else:
            # Genuine conflict if two pages disagree on normalized value.
            conflicting_pages = []
            all_sets = list(per_page_norm.values())
            union = set()
            for s in all_sets:
                union |= s
            if len(union) > 1:
                verdict = "inconsistent"
                for url, norms in per_page_norm.items():
                    conflicting_pages.append({url: sorted(norms)})
                explanation = (
                    f"{field} has {len(unique_norm)} distinct normalized values {unique_norm[:8]!r} "
                    f"across {page_count} pages. This is a real mismatch, not punctuation/spacing. "
                    f"Per-page normalized: {conflicting_pages[:8]!r}."
                )
            else:
                verdict = "consistent"
                explanation = f"{field} normalized to a single value on every page that listed it."

        formatting_only = verdict == "consistent_formatting_only"
        if page_count == 0:
            confidence = 0.12
        elif page_count == 1:
            confidence = min(0.55, _confidence(page_count, len(unique_norm), sources, formatting_only))
        else:
            confidence = _confidence(page_count, len(unique_norm), sources, formatting_only)
        reports.append(
            {
                "field": field,
                "pages_compared": pages_compared,
                "values": unique_raw,
                "normalized_values": unique_norm,
                "confidence": confidence,
                "verdict": verdict,
                "sources": sorted(sources),
                "explanation": explanation,
            }
        )
    return reports


def spread_business_name(extractions: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
    """If the homepage name appears in other pages' text, count those pages too."""
    home_names: list[str] = []
    for item in extractions:
        path = urlparse(item.get("url") or "").path or "/"
        if path in {"", "/"}:
            home_names.extend((item.get("fields") or {}).get("business_name") or [])
    if not home_names:
        return
    canon = home_names[0]
    needle = canon.lower()
    by_url = {(p.get("final_url") or p.get("url")): p for p in pages}
    for item in extractions:
        fields = item.setdefault("fields", {})
        existing = {v.lower() for v in fields.get("business_name") or []}
        if needle in existing:
            continue
        page = by_url.get(item.get("url"))
        html = (page or {}).get("html") or ""
        if needle and needle in html.lower():
            fields.setdefault("business_name", []).append(canon)
            src = item.setdefault("sources", {}).setdefault("business_name", [])
            if "page_mention" not in src:
                src.append("page_mention")


def nap_from_pages(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    extractions = [extract_page_nap(p) for p in pages]
    spread_business_name(extractions, pages)
    report = compare_nap(extractions)
    return extractions, report


def parse_llm_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
