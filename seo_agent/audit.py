"""On-page SEO auditor: search-engine-style markup checks with quoted evidence."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from seo_agent.html_utils import (
    attr_or_none,
    excerpt_markup,
    flatten_schema_types,
    heading_outline,
    json_ld_blocks,
    parse_html,
    same_site,
    visible_text,
)

LOCAL_TYPES = {
    "localbusiness",
    "restaurant",
    "store",
    "foodestablishment",
    "dentist",
    "physician",
    "legalService".lower(),
    "attorney",
    "realestatesagent",
    "electrician",
    "plumber",
    "hvacbusiness",
    "autorepair",
    "hotel",
    "motel",
    "lodgingbusiness",
    "medicalclinic",
    "professionalService".lower(),
}

AEO_TYPES = {"faqpage", "qaPage".lower(), "howto", "article", "newsarticle", "blogposting"}


def _finding(
    metric: str,
    page: str,
    severity: str,
    evidence: str,
    suggested_fix: str,
) -> dict[str, str]:
    return {
        "metric": metric,
        "page": page,
        "severity": severity,
        "evidence": evidence,
        "suggested_fix": suggested_fix,
    }


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text or "")


def _title_of(soup) -> str:
    tag = soup.find("title")
    return tag.get_text(" ", strip=True) if tag else ""


def audit_pages(crawl: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    pages = crawl.get("pages") or []
    start_url = crawl.get("start_url") or ""

    title_map: dict[str, list[str]] = defaultdict(list)
    desc_map: dict[str, list[str]] = defaultdict(list)
    h1_map: dict[str, list[str]] = defaultdict(list)
    ok_pages: list[dict[str, Any]] = []

    robots_status = crawl.get("robots_status") or 0
    if robots_status and robots_status >= 400:
        findings.append(
            _finding(
                "missing_robots_txt",
                crawl.get("robots_url") or start_url,
                "medium",
                f"GET {crawl.get('robots_url')} returned HTTP {robots_status}. Body empty or unavailable.",
                "Publish a robots.txt at the site root that allows indexing of public pages and points to the XML sitemap.",
            )
        )
    if not crawl.get("sitemap_urls"):
        findings.append(
            _finding(
                "missing_xml_sitemap",
                start_url,
                "medium",
                "No sitemap.xml / sitemap_index.xml was discovered from robots.txt or common sitemap paths.",
                "Generate an XML sitemap of indexable URLs and declare it in robots.txt with a Sitemap: directive.",
            )
        )

    for page in pages:
        url = page.get("final_url") or page.get("url") or ""
        status = int(page.get("status") or 0)
        html = page.get("html") or ""
        error = page.get("error")

        if error == "disallowed by robots.txt":
            findings.append(
                _finding(
                    "robots_txt_blocks_page",
                    url,
                    "low",
                    f"robots.txt disallows fetching {url} for the crawler user-agent.",
                    "Allow this URL in robots.txt if it should be indexed; keep it disallowed only if it is intentionally private.",
                )
            )
            continue

        if status >= 400:
            # Common-path probes that 404 are not site issues.
            if page.get("from_probe") and not page.get("from_sitemap"):
                continue
            findings.append(
                _finding(
                    "http_error_status",
                    url,
                    "high" if status >= 500 else "medium",
                    f"HTTP {status} when requesting {page.get('url')}. Error: {error or 'none'}.",
                    "Fix the server/application so this URL returns 200 with the intended document, or 301 it to a live equivalent.",
                )
            )
            continue

        if status and status >= 300 and status < 400:
            continue

        if not html or "html" not in (page.get("content_type") or "html"):
            if error:
                findings.append(
                    _finding(
                        "fetch_error",
                        url,
                        "medium",
                        f"Could not parse HTML ({error}). Content-Type={page.get('content_type')!r}.",
                        "Ensure the URL returns HTML for browsers and crawlers, with a text/html Content-Type.",
                    )
                )
            continue

        soup = parse_html(html)
        parsed = urlparse(url)
        title = _title_of(soup)
        desc = None
        for meta in soup.find_all("meta"):
            name = (meta.get("name") or meta.get("property") or "").lower()
            if name == "description":
                desc = (meta.get("content") or "").strip()
                break
        robots_meta = None
        for meta in soup.find_all("meta"):
            name = (meta.get("name") or "").lower()
            if name == "robots":
                robots_meta = (meta.get("content") or "").strip()
                break
        x_robots = (page.get("headers") or {}).get("x-robots-tag")
        viewport = None
        for meta in soup.find_all("meta"):
            name = (meta.get("name") or "").lower()
            if name == "viewport":
                viewport = (meta.get("content") or "").strip()
                break
        html_tag = soup.find("html")
        lang = attr_or_none(html_tag, "lang") if html_tag else None
        charset = None
        if soup.find("meta", charset=True):
            charset = soup.find("meta", charset=True).get("charset")
        elif soup.find("meta", attrs={"http-equiv": re.compile(r"content-type", re.I)}):
            charset = soup.find("meta", attrs={"http-equiv": re.compile(r"content-type", re.I)}).get("content")
        canonical = None
        for link in soup.find_all("link", rel=True):
            rels = " ".join(link.get("rel") if isinstance(link.get("rel"), list) else [str(link.get("rel"))]).lower()
            if "canonical" in rels:
                canonical = (link.get("href") or "").strip()
                break
        h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
        outline = heading_outline(soup)
        text = visible_text(soup)
        word_count = len(_words(text))
        blocks = json_ld_blocks(soup)
        schema_types = []
        for block in blocks:
            schema_types.extend(flatten_schema_types(block))
        schema_l = {t.lower() for t in schema_types}

        og_title = None
        og_desc = None
        og_image = None
        for meta in soup.find_all("meta"):
            prop = (meta.get("property") or meta.get("name") or "").lower()
            if prop == "og:title":
                og_title = (meta.get("content") or "").strip()
            elif prop == "og:description":
                og_desc = (meta.get("content") or "").strip()
            elif prop == "og:image":
                og_image = (meta.get("content") or "").strip()

        imgs = soup.find_all("img")
        missing_alt = []
        for img in imgs:
            if img.has_attr("alt"):
                continue
            src = img.get("src") or img.get("data-src") or "[no src]"
            missing_alt.append(str(src)[:80])

        if parsed.scheme != "https":
            findings.append(
                _finding(
                    "not_https",
                    url,
                    "high",
                    f"Final URL scheme is {parsed.scheme!r} ({url}). Search engines treat HTTPS as a ranking and trust signal.",
                    "Serve the page over HTTPS and 301 HTTP → HTTPS, including canonical and internal links.",
                )
            )

        chain = page.get("redirect_chain") or []
        if len(chain) >= 3:
            findings.append(
                _finding(
                    "redirect_chain",
                    url,
                    "medium",
                    f"Request followed {len(chain)} hops: {' → '.join(chain[:6])} → {url}.",
                    "Collapse the chain into a single 301 from the requested URL to the final document.",
                )
            )

        if not soup.find("title"):
            findings.append(
                _finding(
                    "missing_title",
                    url,
                    "high",
                    "No <title> element in the document head. Snippet of <head>: "
                    + excerpt_markup(soup.find("head")),
                    "Add a unique <title> of roughly 50–60 characters that names the page topic and the business.",
                )
            )
        elif not title:
            findings.append(
                _finding(
                    "empty_title",
                    url,
                    "high",
                    f"Empty title tag: {excerpt_markup(soup.find('title'))}",
                    "Put a descriptive, unique title inside the <title> element.",
                )
            )
        else:
            if len(title) < 15:
                findings.append(
                    _finding(
                        "title_too_short",
                        url,
                        "medium",
                        f"<title> is {len(title)} characters: {title!r}. Crawlers treat very short titles as weak relevance signals.",
                        "Expand the title to ~50–60 characters with the primary topic and brand.",
                    )
                )
            elif len(title) > 65:
                findings.append(
                    _finding(
                        "title_too_long",
                        url,
                        "low",
                        f"<title> is {len(title)} characters (likely truncated in SERPs): {title!r}",
                        "Shorten the title to about 50–60 characters, leading with the most important words.",
                    )
                )
            title_map[title.strip().lower()].append(url)

        if not desc:
            findings.append(
                _finding(
                    "missing_meta_description",
                    url,
                    "medium",
                    "No <meta name='description'> found in <head>. "
                    + excerpt_markup(soup.find("head")),
                    "Add a unique 120–160 character meta description that states the page's value and includes a call to action.",
                )
            )
        else:
            if len(desc) < 50:
                findings.append(
                    _finding(
                        "meta_description_too_short",
                        url,
                        "low",
                        f"Meta description is {len(desc)} characters: {desc!r}",
                        "Write a 120–160 character description that summarises the page.",
                    )
                )
            elif len(desc) > 165:
                findings.append(
                    _finding(
                        "meta_description_too_long",
                        url,
                        "low",
                        f"Meta description is {len(desc)} characters and will likely be truncated: {desc[:180]!r}",
                        "Trim the meta description to ~150–160 characters.",
                    )
                )
            desc_map[desc.strip().lower()].append(url)

        robots_blob = " ".join(x for x in [robots_meta, x_robots] if x).lower()
        if "noindex" in robots_blob:
            findings.append(
                _finding(
                    "noindex_directive",
                    url,
                    "high",
                    f"Indexing is blocked. meta robots={robots_meta!r}; x-robots-tag={x_robots!r}.",
                    "Remove noindex from public pages you want in search results.",
                )
            )

        if not viewport:
            findings.append(
                _finding(
                    "missing_viewport",
                    url,
                    "high",
                    "No <meta name='viewport'> in the document. Mobile-first indexing expects a viewport tag.",
                    "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">.",
                )
            )

        if not lang:
            findings.append(
                _finding(
                    "missing_html_lang",
                    url,
                    "medium",
                    f"<html> has no lang attribute: {excerpt_markup(html_tag) or '<html> missing'}",
                    "Set lang on the root HTML element, e.g. <html lang=\"en\">.",
                )
            )

        if not charset:
            findings.append(
                _finding(
                    "missing_charset",
                    url,
                    "low",
                    "No <meta charset> or Content-Type http-equiv in the document head.",
                    "Add <meta charset=\"utf-8\"> as the first child of <head>.",
                )
            )

        if not canonical:
            findings.append(
                _finding(
                    "missing_canonical",
                    url,
                    "medium",
                    "No <link rel='canonical'> in the document head.",
                    "Add a self-referencing canonical to the preferred absolute URL of this page.",
                )
            )

        if not h1s:
            findings.append(
                _finding(
                    "missing_h1",
                    url,
                    "high",
                    "0 <h1> elements. Heading outline: "
                    + (str(outline[:8]) if outline else "no h1–h6 headings at all")
                    + ".",
                    "Add exactly one H1 that describes the primary topic of the page.",
                )
            )
        else:
            empty = [h for h in h1s if not h]
            if empty:
                findings.append(
                    _finding(
                        "empty_h1",
                        url,
                        "medium",
                        f"Found {len(h1s)} <h1> tags; {len(empty)} are empty. Markup: "
                        + excerpt_markup(soup.find("h1")),
                        "Put visible, descriptive text inside the H1.",
                    )
                )
            if len(h1s) > 1:
                findings.append(
                    _finding(
                        "multiple_h1",
                        url,
                        "medium",
                        f"{len(h1s)} <h1> tags on the page: {h1s[:6]!r}.",
                        "Keep a single H1; demote additional headings to H2+.",
                    )
                )
            for h in h1s:
                if h:
                    h1_map[h.strip().lower()].append(url)

        levels = [o["level"] for o in outline]
        for prev, cur in zip(levels, levels[1:]):
            if cur > prev + 1:
                findings.append(
                    _finding(
                        "heading_hierarchy_skip",
                        url,
                        "low",
                        f"Heading levels jump from h{prev} to h{cur}. Outline: "
                        + ", ".join(f"{o['tag']}:{o['text'][:40]!r}" for o in outline[:10]),
                        "Do not skip heading levels (e.g. H2 → H4). Nest headings in order.",
                    )
                )
                break

        if word_count < 150 and status == 200:
            findings.append(
                _finding(
                    "thin_content",
                    url,
                    "medium",
                    f"Visible text word count is {word_count}. First 180 characters: {text[:180]!r}",
                    "Add unique, useful copy that answers the query a searcher would use for this URL.",
                )
            )

        if imgs and missing_alt:
            sample = ", ".join(missing_alt[:5])
            findings.append(
                _finding(
                    "images_missing_alt",
                    url,
                    "medium",
                    f"{len(missing_alt)} of {len(imgs)} <img> tags have no alt attribute. Examples: {sample}",
                    "Add descriptive alt text to informative images; use alt=\"\" only for purely decorative images.",
                )
            )

        if not og_title or not og_desc:
            findings.append(
                _finding(
                    "missing_open_graph",
                    url,
                    "low",
                    f"og:title={og_title!r}; og:description={og_desc!r}; og:image={og_image!r}",
                    "Add og:title, og:description, and og:image so search/answer engines and social crawlers get a clean snippet.",
                )
            )

        if not blocks:
            findings.append(
                _finding(
                    "missing_json_ld",
                    url,
                    "medium",
                    "No application/ld+json blocks parsed from the page.",
                    "Add JSON-LD for Organization/LocalBusiness on the homepage and Article/FAQPage where the content supports it.",
                )
            )

        click_here = []
        for a in soup.find_all("a"):
            label = a.get_text(" ", strip=True).lower()
            if label in {"click here", "here", "read more", "more", "link"}:
                click_here.append((label, (a.get("href") or "")[:80]))
        if click_here:
            findings.append(
                _finding(
                    "generic_anchor_text",
                    url,
                    "low",
                    f"{len(click_here)} links use non-descriptive anchors such as {click_here[:4]!r}.",
                    "Use descriptive anchor text that names the destination page.",
                )
            )

        internal = 0
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            if href.startswith("#") or href.lower().startswith("mailto:") or href.lower().startswith("tel:"):
                continue
            try:
                from seo_agent.html_utils import normalize_url

                abs_url = normalize_url(href, url)
            except Exception:
                continue
            if same_site(abs_url, url):
                internal += 1
        if internal == 0 and status == 200:
            findings.append(
                _finding(
                    "no_internal_links",
                    url,
                    "medium",
                    "Page has 0 same-site <a href> links. Crawlers rely on internal links to discover and weight URLs.",
                    "Add contextual internal links to related pages (contact, about, key services).",
                )
            )

        low_title = (title or "").lower()
        if any(p in low_title for p in ("404", "not found", "page not found")) and status == 200:
            findings.append(
                _finding(
                    "soft_404",
                    url,
                    "high",
                    f"HTTP 200 but title looks like an error page: {title!r}",
                    "Return a real 404/410 status for missing documents instead of a 200 error template.",
                )
            )

        ok_pages.append(
            {
                "url": url,
                "schema_l": schema_l,
                "has_local": bool(schema_l & LOCAL_TYPES),
                "has_aeo": bool(schema_l & AEO_TYPES),
                "has_org": "organization" in schema_l or "localbusiness" in schema_l,
                "favicon": bool(soup.find("link", rel=re.compile(r"icon", re.I))),
                "html": html,
                "soup_has_faq": "faqpage" in schema_l,
            }
        )

    for title, urls in title_map.items():
        if len(set(urls)) > 1:
            findings.append(
                _finding(
                    "duplicate_title",
                    urls[0],
                    "high",
                    f"Title {title!r} is reused on {len(set(urls))} URLs: {list(dict.fromkeys(urls))[:6]}",
                    "Give each indexable URL a unique title that reflects its distinct topic.",
                )
            )
    for desc, urls in desc_map.items():
        if desc and len(set(urls)) > 1:
            findings.append(
                _finding(
                    "duplicate_meta_description",
                    urls[0],
                    "medium",
                    f"Meta description reused on {len(set(urls))} URLs. First 120 chars: {desc[:120]!r}. Pages: {list(dict.fromkeys(urls))[:6]}",
                    "Write a unique meta description per URL.",
                )
            )
    for h1, urls in h1_map.items():
        if h1 and len(set(urls)) > 1:
            findings.append(
                _finding(
                    "duplicate_h1",
                    urls[0],
                    "low",
                    f"H1 {h1!r} is reused on {len(set(urls))} URLs: {list(dict.fromkeys(urls))[:6]}",
                    "Vary H1s so each page's primary heading matches its unique intent.",
                )
            )

    homepage = start_url
    home_ok = next((p for p in ok_pages if p["url"].rstrip("/") == homepage.rstrip("/")), None)
    any_local = any(p["has_local"] or p["has_org"] for p in ok_pages)
    if ok_pages and not any_local:
        findings.append(
            _finding(
                "missing_localbusiness_schema",
                homepage,
                "medium",
                f"Crawled {len(ok_pages)} HTML pages; none contained JSON-LD @type LocalBusiness (or a subtype) / Organization.",
                "Add LocalBusiness JSON-LD on the homepage and contact page with name, address, telephone, and url.",
            )
        )
    any_aeo = any(p["has_aeo"] for p in ok_pages)
    if ok_pages and not any_aeo:
        findings.append(
            _finding(
                "missing_aeo_schema",
                homepage,
                "low",
                "No FAQPage, QAPage, HowTo, or Article JSON-LD was found on crawled pages. Answer engines prefer explicit Q&A markup.",
                "Where the page truly contains FAQs or a how-to, mark them up with FAQPage/HowTo JSON-LD. Do not invent FAQs.",
            )
        )
    if home_ok and not home_ok.get("favicon"):
        findings.append(
            _finding(
                "missing_favicon",
                homepage,
                "low",
                "Homepage <head> has no rel=icon / shortcut icon / apple-touch-icon link.",
                "Add a <link rel=\"icon\" href=\"/favicon.ico\"> (and an SVG/PNG variant).",
            )
        )

    # Stable order, drop exact duplicates.
    uniq = []
    seen = set()
    for f in findings:
        key = (f["metric"], f["page"], f["evidence"][:180])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    return uniq
