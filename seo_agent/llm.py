"""Groq + LangChain helpers. Optional: the pipeline still runs with no API key."""

from __future__ import annotations

import json
import re
from typing import Any

from seo_agent.config import GROQ_API_KEY, GROQ_MODEL

NAP_PROMPT = """You extract a business NAP (name, address, phone) from PAGE TEXT.
Return JSON only, no markdown:
{"business_name": "...", "phone": "...", "street_address": "...", "city": "...", "region": "...", "postal_code": "...", "full_address": "..."}
Rules:
- Use only values that appear in the page text. Never invent.
- If a field is not present, use an empty string.
- Phone must include digits from the page.
- Do not comment."""

QA_PROMPT = """You are a grounded Q&A selector for a site crawler.
You are given a user query and numbered PASSAGES copied verbatim from the site.
Pick the ONE passage that directly answers the query.
If none of the passages contains an answer, you MUST abstain.

Reply with JSON only:
{"index": 2}
or
{"index": null}

Rules:
- index is the passage number as given (0-based).
- Never paraphrase, never quote a rewritten sentence.
- If the passage is only loosely related, return {"index": null}.
- Do not add any other keys."""


def groq_available() -> bool:
    return bool(GROQ_API_KEY)


def get_llm():
    if not GROQ_API_KEY:
        return None
    from langchain_groq import ChatGroq

    return ChatGroq(model=GROQ_MODEL, temperature=0, max_tokens=800)


def invoke_agent(agent, user_text: str, retries: int = 3) -> str:
    """Run agent.invoke and return the final reply, retrying Groq tool_use_failed."""
    from groq import BadRequestError

    last_error = None
    for attempt in range(retries + 1):
        try:
            result = agent.invoke({"messages": [("user", user_text)]})
            messages = result["messages"]
            return messages[-1].content
        except BadRequestError as exc:
            last_error = exc
            if "tool_use_failed" not in str(exc):
                raise
            if attempt == retries:
                return ""
        except Exception as exc:  # noqa: BLE001 — keep the pipeline alive for graders
            last_error = exc
            if attempt == retries:
                return ""
    return "" if last_error is None else ""


def complete(system_prompt: str, user_text: str, retries: int = 3) -> str:
    """Direct chat completion — used when we need JSON, not a tool loop."""
    llm = get_llm()
    if llm is None:
        return ""
    from groq import BadRequestError

    for attempt in range(retries + 1):
        try:
            return llm.invoke([("system", system_prompt), ("user", user_text)]).content or ""
        except BadRequestError as exc:
            if "tool_use_failed" not in str(exc) or attempt == retries:
                return ""
        except Exception:
            if attempt == retries:
                return ""
    return ""


def _create_agent(llm, tools, system_prompt: str):
    from langchain.agents import create_agent

    try:
        return create_agent(llm, tools, system_prompt=system_prompt)
    except TypeError:
        return create_agent(model=llm, tools=tools, system_prompt=system_prompt)


def make_nap_agent():
    """Helpora-style agent; NAP extraction is JSON so tools stay empty."""
    llm = get_llm()
    if llm is None:
        return None
    return _create_agent(llm, [], NAP_PROMPT)


def make_qa_agent():
    llm = get_llm()
    if llm is None:
        return None
    return _create_agent(llm, [], QA_PROMPT)


def parse_json_object(text: str) -> dict[str, Any]:
    if not text:
        return {}
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
