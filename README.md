# SEO / Local SEO / AEO audit agent

LangChain + LangGraph agents that take **one web URL** (and, for Question 3, a natural-language query) and emit evidence-backed JSON. No paid APIs, key-gated data vendors, or paid connectors.

The same pipeline is used for every site. Nothing is hard-coded to a demo domain.

## Questions and deliverables

| Question | Agent | Output |
| --- | --- | --- |
| 1. On-page auditor | Search-engine-style crawler + markup checks | `outputs/audit.json` |
| 2. NAP consistency | Name / address / phone extraction and comparison | `outputs/nap_report.json` |
| 3. Grounded Q&A | Site-wide retrieval; **verbatim** excerpt or `null` | `outputs/answer.json` |

### `audit.json`

One object per finding:

```json
{
  "metric": "missing_h1",
  "page": "https://example.com/about",
  "severity": "high",
  "evidence": "0 <h1> elements. Heading outline: h2:'About us'.",
  "suggested_fix": "Add exactly one H1 that describes the primary topic of the page."
}
```

Checks include title and meta description quality/duplication, H1s, heading skips, canonical, viewport, `lang`, charset, HTTPS, thin content, image alt, Open Graph, JSON-LD / LocalBusiness / FAQ schema, generic anchors, internal linking, `noindex`, redirect chains, robots.txt, and XML sitemaps. Evidence is quoted from the fetched markup.

### `nap_report.json`

One object per field (`business_name`, `phone`, `street_address`, `city`, `region`, `postal_code`, `full_address`):

```json
{
  "field": "phone",
  "pages_compared": ["https://example.com/", "https://example.com/contact"],
  "values": ["(212) 555-0100", "212-555-0100"],
  "normalized_values": ["2125550100"],
  "confidence": 0.84,
  "verdict": "consistent_formatting_only",
  "explanation": "Raw strings differ but collapse to one normalized value."
}
```

Verdicts:

- `consistent` — same value across pages
- `consistent_formatting_only` — punctuation/spacing/abbreviation only (not a real NAP mismatch)
- `inconsistent` — normalized values disagree
- `insufficient_evidence` — fewer than two pages yielded that field

Extraction uses JSON-LD, microdata, `tel:` links, and regex. If `GROQ_API_KEY` is set, a LangChain agent may propose extra NAP values; they are **dropped unless they already appear on the page**.

### `answer.json`

```json
{
  "query": "What are your hours?",
  "url": "https://example.com/visit",
  "excerpt": "Open daily 8am–10pm, kitchen until 9:30pm."
}
```

`url` and `excerpt` are `null` when the crawled site does not support an answer. The excerpt is copied from the page, never paraphrased. A verbatim guardrail rejects any span that is not a substring of the page text (same idea as Helpora’s output guardrail: check the model’s output, not the input).

## How to run

Python 3.10+. Groq is **optional** (free tier). Crawler, on-page audit, schema/regex NAP, and lexical Q&A all run with no key.

```powershell
cd seo-audit-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# optional: put GROQ_API_KEY in .env

python -m seo_agent --url https://www.katzsdelicatessen.com --query "What are the hours of operation?"
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m seo_agent --url "https://www.katzsdelicatessen.com" --query "What are the hours of operation?"
```

Flags: `--max-pages` (default 40), `--out-dir` (default `outputs/`).

Outputs written:

- `outputs/audit.json`
- `outputs/nap_report.json`
- `outputs/answer.json`
- `outputs/demo_url.txt` — input URL for this run
- `outputs/query.txt` — Question 3 query
- `outputs/run_meta.json` — crawl size and whether Groq was used

## Architecture

Helpora-style graph, not a one-shot prompt:

```
START → crawl → audit → nap → qa → write → END
```

- **Crawl** — robots.txt, XML sitemap, BFS same-site links, common local-SEO paths (`/contact`, `/about`, `/locations`, …). Optional fetch of citation hosts the site itself links to (Yelp, Maps, …).
- **Audit** — deterministic HTML checks so findings stay reviewable.
- **NAP** — extract → normalize (phones to digits, street abbreviations) → verdict + confidence explanation.
- **Q&A** — paragraph chunks → BM25 (and ChromaDB when `chromadb` is installed, Helpora Session 4) → `search_site` tool → Groq picks a passage **index** → verbatim guardrail. If Groq is absent, the top lexical hit is used only when query terms actually appear.

LLM: `ChatGroq` with `openai/gpt-oss-120b` (Groq free tier, same model as the Helpora notebook). Override with `GROQ_MODEL`. `invoke_agent` retries Groq’s intermittent `tool_use_failed` error.

## Demo run

Input URL: `https://www.katzsdelicatessen.com`

Question 3 query: `What are the hours of operation?`

That run wrote:

- `outputs/audit.json` — 128 markup findings (missing H1s, short/duplicate meta descriptions, no canonical, no JSON-LD, images without alt, …) each with a quote from the live HTML
- `outputs/nap_report.json` — phone/address consistent across pages (`212-254-2246` vs `(212) 254-2246` counted as formatting only); name `Katz's Delicatessen`
- `outputs/answer.json` — verbatim excerpt from `https://katzsdelicatessen.com/address` (restaurant hours), not a paraphrase

If the site has no supporting passage, `url` and `excerpt` are `null`.

## What is not used

No Ahrefs, SEMrush, Moz, Google APIs, Places, SerpAPI, or other paid/key-gated SEO data. Fetching is ordinary HTTP with BeautifulSoup. Embeddings are local (Chroma default / lexical fallback). Groq is an optional free-tier LLM, not a data vendor.
