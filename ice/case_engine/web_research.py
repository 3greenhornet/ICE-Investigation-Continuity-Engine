"""case_engine/web_research.py — keyless web research for ICE.

Gap closed (CHANGES.md "Web research backend"): the SEARCH executor could only
fetch a URL a human typed in, and only through paid scrapers (Scrapfly /
Firecrawl). Natural-language searches were marked UNSUPPORTED, so ICE could not
look anything up on its own. This module uses maintained libraries instead of
hand-rolled scraping:

  * ddgs         — DuckDuckGo metasearch, no API key            (search_web)
  * GitHub REST  — public repository search, no key (rate-limited) (search_github)
  * trafilatura  — main-text extraction from HTML                (fetch_page_text)

All three are optional: a missing package or network failure returns an empty
result with a reason, never an exception into the investigation cycle.

Plane: control-side helpers. They never write ICE tables; callers ingest the
results through case_engine.evidence like every other evidence source.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; ICE-research/1.0; +https://github.com/)"
_TIMEOUT = float(os.getenv("ICE_WEB_TIMEOUT", "15"))
_MAX_BYTES = int(os.getenv("ICE_WEB_MAX_BYTES", str(3 * 1024 * 1024)))
MAX_TEXT_CHARS = int(os.getenv("ICE_WEB_MAX_TEXT", "12000"))


_TEMPLATE_PREFIXES = (
    r"what techniques, libraries or tools best achieve this goal \(latest best practices and alternatives\):",
    r"where in the codebase is this implemented and what currently limits it:",
    r"what evidence shows the current implementation does not yet meet this goal:",
    r"what benchmarks or failure modes are known for approaches to this goal:",
    r"what prior-art open-source projects or papers already solve this goal:",
    r"who is affected if this goal is not met, and how would they notice:",
    r"what would an expert in this lens ask first about this goal:",
    r"from the [a-z /-]+ perspective:",
)


def distill_query(text: str) -> str:
    """Turn a generated research question / gap text into a search query:
    drop the "[lens]" tag and the question template, keep the goal itself."""
    q = re.sub(r"^\s*\[[^\]]+\]\s*", "", text or "")
    for pat in _TEMPLATE_PREFIXES:
        q = re.sub(pat, "", q, flags=re.IGNORECASE)
    q = re.sub(r"\s+", " ", q).strip(" ?:.")
    return q[:300] or (text or "")[:300]


def web_research_enabled() -> bool:
    """ICE_WEB_RESEARCH=0 turns every outbound lookup off (air-gapped use)."""
    return os.getenv("ICE_WEB_RESEARCH", "1").lower() not in ("0", "false", "no")


# --------------------------------------------------------------------------- search

def _search_web_sync(query: str, max_results: int) -> list[dict]:
    from ddgs import DDGS  # optional dependency

    out = []
    with DDGS() as d:
        for r in d.text(query, max_results=max_results) or []:
            url = r.get("href") or r.get("url")
            if url and url.startswith(("http://", "https://")):
                out.append({"title": r.get("title", ""), "url": url,
                            "snippet": r.get("body", "")})
    return out


async def search_web(query: str, max_results: int = 5) -> dict:
    """Search the public web. Returns {"results": [...], "error": str|None}."""
    query = (query or "").strip()
    if not query:
        return {"results": [], "error": "empty query"}
    if not web_research_enabled():
        return {"results": [], "error": "web research disabled (ICE_WEB_RESEARCH=0)"}
    try:
        results = await asyncio.to_thread(_search_web_sync, query[:300], max(1, min(max_results, 20)))
        return {"results": results, "error": None, "engine": "duckduckgo"}
    except ImportError:
        return {"results": [], "error": "package 'ddgs' not installed"}
    except Exception as exc:  # rate limit, network, parser change
        logger.warning("web search failed for %r: %s", query[:80], exc)
        return {"results": [], "error": f"{type(exc).__name__}: {exc}"[:300]}


_GH_STOP = {
    "a", "an", "the", "for", "to", "of", "in", "on", "with", "and", "or", "than", "that",
    "is", "are", "be", "how", "what", "which", "best", "fast", "faster", "better", "good",
    "latest", "alternatives", "alternative", "library", "libraries", "tool", "tools",
    "open", "source", "github", "repo", "repository", "python", "using", "use", "vs",
}


def github_keywords(query: str, limit: int = 4) -> str:
    """GitHub repository search ANDs every term, so a sentence returns nothing.
    Keep the few most specific words (longest first, de-duplicated)."""
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]{1,}", query.lower()) if w not in _GH_STOP]
    seen, picked = set(), []
    for w in sorted(dict.fromkeys(words), key=lambda x: (-len(x), words.index(x))):
        if w not in seen:
            seen.add(w)
            picked.append(w)
        if len(picked) >= limit:
            break
    # keep the original order of the chosen words
    return " ".join(sorted(picked, key=words.index))


async def search_github(query: str, max_results: int = 5) -> dict:
    """Search public GitHub repositories (stars, recency, description).
    Uses GITHUB_TOKEN when set (higher rate limit); works without it."""
    query = (query or "").strip()
    if not query:
        return {"results": [], "error": "empty query"}
    if not web_research_enabled():
        return {"results": [], "error": "web research disabled (ICE_WEB_RESEARCH=0)"}
    import httpx

    headers = {"Accept": "application/vnd.github+json", "User-Agent": _UA}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": query[:250], "sort": "stars", "order": "desc",
                        "per_page": max(1, min(max_results, 20))},
            )
        if resp.status_code != 200:
            return {"results": [], "error": f"GitHub API HTTP {resp.status_code}"}
        items = resp.json().get("items", [])
        return {"error": None, "engine": "github", "results": [{
            "title": it.get("full_name"), "url": it.get("html_url"),
            "snippet": it.get("description") or "",
            "stars": it.get("stargazers_count"), "updated": it.get("pushed_at"),
            "language": it.get("language"), "license": (it.get("license") or {}).get("spdx_id"),
            "archived": it.get("archived", False),
        } for it in items]}
    except Exception as exc:
        logger.warning("GitHub search failed for %r: %s", query[:80], exc)
        return {"results": [], "error": f"{type(exc).__name__}: {exc}"[:300]}


# --------------------------------------------------------------------------- fetch

async def fetch_page_text(url: str) -> dict:
    """Download a page (size/time bounded) and extract its main text."""
    if not url.startswith(("http://", "https://")):
        return {"url": url, "text": "", "error": "not an http(s) URL"}
    if not web_research_enabled():
        return {"url": url, "text": "", "error": "web research disabled (ICE_WEB_RESEARCH=0)"}
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True,
                                     headers={"User-Agent": _UA}) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return {"url": url, "text": "", "error": f"HTTP {resp.status_code}"}
                ctype = resp.headers.get("content-type", "")
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_BYTES:
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks)
                final_url = str(resp.url)
        if "html" not in ctype and "xml" not in ctype:
            text = raw.decode("utf-8", errors="replace") if ctype.startswith("text/") else ""
            title = ""
        else:
            html = raw.decode("utf-8", errors="replace")
            text, title = await asyncio.to_thread(_extract, html, final_url)
        text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
        return {"url": final_url, "title": title, "text": text[:MAX_TEXT_CHARS],
                "truncated": len(text) > MAX_TEXT_CHARS, "error": None if text else "no extractable text"}
    except Exception as exc:
        logger.warning("fetch failed for %s: %s", url, exc)
        return {"url": url, "text": "", "error": f"{type(exc).__name__}: {exc}"[:300]}


def _extract(html: str, url: str) -> tuple[str, str]:
    try:
        import trafilatura

        text = trafilatura.extract(html, url=url, include_comments=False,
                                   include_tables=True, favor_precision=True) or ""
        meta = trafilatura.extract_metadata(html, default_url=url)
        return text, (meta.title if meta and meta.title else "")
    except ImportError:
        # Minimal fallback: strip tags. Worse than trafilatura, never a crash.
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return re.sub(r"[ \t]+", " ", text), ""


# --------------------------------------------------------------------------- combined

async def research(query: str, max_results: int = 3, include_github: bool = False,
                   fetch_pages: bool = True) -> dict:
    """Search, optionally fetch the top pages, and return evidence-ready items.

    Each item: {"title", "url", "content", "kind": "web"|"github", ...}. Pages
    that cannot be fetched fall back to the search snippet (marked as such).
    """
    web = await search_web(query, max_results=max_results)
    items: list[dict[str, Any]] = []
    pages = []
    if fetch_pages and web["results"]:
        pages = await asyncio.gather(*(fetch_page_text(r["url"]) for r in web["results"]))
    for i, r in enumerate(web["results"]):
        page = pages[i] if pages else {}
        body = page.get("text") or ""
        items.append({
            "kind": "web", "title": r["title"] or page.get("title", ""), "url": r["url"],
            "content": body or r["snippet"], "from_snippet_only": not body,
            "snippet": r["snippet"],
        })
    errors = [e for e in [web.get("error")] if e]
    if include_github:
        kw = github_keywords(query)
        gh = await search_github(kw or query, max_results=max_results)
        if not gh.get("results") and len(kw.split()) > 2:
            gh = await search_github(" ".join(kw.split()[:2]), max_results=max_results)
        if gh.get("error"):
            errors.append(gh["error"])
        for r in gh["results"]:
            items.append({
                "kind": "github", "title": r["title"], "url": r["url"],
                "content": (f"GitHub repository {r['title']} — {r['snippet']}\n"
                            f"stars: {r['stars']} · language: {r['language']} · license: {r['license']} "
                            f"· last push: {r['updated']}{' · ARCHIVED' if r['archived'] else ''}"),
                "stars": r["stars"], "updated": r["updated"], "archived": r["archived"],
            })
    return {"query": query, "items": items, "errors": errors}


async def plan_queries(goal_text: str, case_id: Optional[str] = None, n: int = 2) -> dict:
    """Rewrite an outcome-style goal ("answer questions in <10 s on a laptop")
    into short technical web queries and GitHub keyword queries — query
    rewriting, the standard fix for goals that are not phrased as searches.
    Uses ICE's LLM chain (logged in the LLM ledger) when available; otherwise
    returns the distilled text. Never raises."""
    base = distill_query(goal_text)
    fallback = {"web": [base], "github": [github_keywords(base)], "planned_by": "distill"}
    if os.getenv("ICE_WEB_QUERY_PLANNING", "1").lower() in ("0", "false", "no"):
        return fallback
    try:
        from case_engine.llm import _call_llm_json, llm_available

        if not llm_available():
            return fallback
        out = await _call_llm_json(
            prompt=(
                "A software team states this goal for their codebase:\n"
                f"\"{base}\"\n\n"
                f"Write {n} web search queries (5-9 words, technical terms, no quotes) that would find "
                f"concrete techniques, tools or benchmarks to achieve it, and {n} GitHub repository "
                "search queries (2-3 keywords each, e.g. 'onnx reranker'). Return JSON: "
                '{"web": ["..."], "github": ["..."]}'
            ),
            system_prompt="You turn engineering goals into precise search queries. Return ONLY valid JSON.",
            temperature=0.2, role="GENERAL", operation="web_query_planning",
            prompt_id="web_query_planning", prompt_version="v1", case_id=case_id, max_retries=1,
        )
        web = [q.strip() for q in (out or {}).get("web", []) if isinstance(q, str) and q.strip()][:n]
        gh = [q.strip() for q in (out or {}).get("github", []) if isinstance(q, str) and q.strip()][:n]
        if not web:
            return fallback
        return {"web": web, "github": gh or [github_keywords(base)], "planned_by": "llm"}
    except Exception as exc:
        logger.debug("query planning failed, using distilled text: %s", exc)
        return fallback


_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "best", "by", "can", "code",
    "do", "does", "example", "examples", "for", "from", "guide", "how", "in",
    "into", "is", "it", "its", "of", "on", "or", "overview", "that", "the",
    "their", "this", "to", "tutorial", "use", "using", "vs", "what", "when",
    "where", "which", "with", "why",
})

_BLOCKWALL_MARKERS = (
    "enable javascript",
    "javascript is disabled",
    "access denied",
    "403 forbidden",
    "404 not found",
    "page not found",
    "sign in to continue",
    "log in to continue",
    "subscribe to read",
    "verify you are human",
    "checking your browser",
    "accept cookies",
)

_HIGH_SIGNAL_HOSTS = (
    "github.com",
    "gitlab.com",
    "arxiv.org",
    "aclanthology.org",
    "semanticscholar.org",
    "openalex.org",
    "dl.acm.org",
    "ieeexplore.ieee.org",
    "pypi.org",
    "readthedocs.io",
    "readthedocs.org",
    "docs.python.org",
    "fastapi.tiangolo.com",
    "postgresql.org",
    "sqlite.org",
)

_LOW_SIGNAL_PATH_MARKERS = (
    "/login",
    "/signin",
    "/signup",
    "/pricing",
    "/privacy",
    "/terms",
    "/cookie",
    "/tag/",
    "/category/",
    "/author/",
)


def _informative_tokens(text: str) -> list[str]:
    """Extract lowercase technical tokens (splitting camelCase/snake_case too)."""
    import re

    if not text:
        return []
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    raw = re.findall(r"[A-Za-z0-9]{2,}", expanded.lower())
    seen: list[str] = []
    seen_set: set[str] = set()
    for tok in raw:
        if tok in _STOPWORDS:
            continue
        if tok not in seen_set:
            seen_set.add(tok)
            seen.append(tok)
    return seen


def _stem_match(q_tok: str, doc_tokens: set[str], doc_text: str) -> float:
    """Exact match = 1.0, prefix/stem match (>=4 chars) = 0.7, else 0.0."""
    if q_tok in doc_tokens:
        return 1.0
    if len(q_tok) >= 4:
        stem = q_tok[: max(4, len(q_tok) - 2)]
        if any(dt.startswith(stem) or q_tok.startswith(dt[:4]) for dt in doc_tokens if len(dt) >= 4):
            return 0.75
        if q_tok in doc_text:
            return 0.6
    return 0.0


def score_research_relevance(query: str, item: dict) -> tuple[float, list[str]]:
    """Score a search result item in [0.0, 1.0] for relevance and signal quality."""
    from urllib.parse import urlparse

    reasons: list[str] = []
    title = str(item.get("title") or "").strip()
    snippet = str(item.get("snippet") or item.get("summary") or item.get("text") or "").strip()
    url = str(item.get("url") or "").strip()

    combined_lower = f"{title}\n{snippet}\n{url}".lower()
    if any(marker in combined_lower for marker in _BLOCKWALL_MARKERS):
        return 0.0, ["blocked_or_error_page"]

    if not title and len(snippet) < 25:
        return 0.0, ["empty_content"]

    parsed = urlparse(url) if url else None
    host = (parsed.netloc or "").lower() if parsed else ""
    path = (parsed.path or "").lower() if parsed else ""

    if any(marker in path for marker in _LOW_SIGNAL_PATH_MARKERS):
        reasons.append("low_signal_url_path")

    title_tokens = set(_informative_tokens(title))
    body_tokens = set(_informative_tokens(snippet))
    url_tokens = set(_informative_tokens(f"{host} {path}"))
    all_tokens = title_tokens | body_tokens | url_tokens

    query_tokens = _informative_tokens(query)
    if query_tokens:
        total_weight = 0.0
        matched_weight = 0.0
        title_hits = 0
        for qt in query_tokens:
            # Longer / non-trivial tokens carry more specificity weight
            w = 1.4 if len(qt) >= 7 else 1.0
            total_weight += w
            in_title = _stem_match(qt, title_tokens, title.lower())
            in_url = _stem_match(qt, url_tokens, url.lower())
            in_body = _stem_match(qt, body_tokens, snippet.lower())
            best = max(in_title, 0.9 * in_url, 0.85 * in_body)
            if in_title >= 0.75:
                title_hits += 1
            matched_weight += w * best
        coverage = matched_weight / max(total_weight, 1e-6)
        title_ratio = title_hits / len(query_tokens)
    else:
        coverage = 0.35 if (title or len(snippet) >= 60) else 0.1
        title_ratio = 0.0

    score = 0.62 * coverage + 0.18 * title_ratio

    # Engineering / scholarly host bonus only amplifies genuine topical matches
    is_high_signal_host = any(host == h or host.endswith("." + h) for h in _HIGH_SIGNAL_HOSTS)
    if is_high_signal_host:
        score += 0.14 if coverage >= 0.15 else 0.04
        reasons.append("high_signal_source")

    if len(snippet) >= 80:
        score += 0.08
    elif len(snippet) < 35:
        score -= 0.12
        reasons.append("thin_snippet")

    if "low_signal_url_path" in reasons:
        score -= 0.25

    if len(all_tokens) < 4:
        score -= 0.15
        reasons.append("low_information_density")

    score = round(max(0.0, min(1.0, score)), 4)
    if coverage >= 0.45:
        reasons.append("strong_query_match")
    elif coverage < 0.15:
        reasons.append("weak_query_match")
    return score, reasons


def filter_research_items(
    query: str,
    items: list[dict],
    *,
    min_relevance: float = 0.18,
) -> tuple[list[dict], list[dict]]:
    """Score, deduplicate by canonical URL, and partition items into (kept, filtered_out)."""
    from urllib.parse import urlparse, urlunparse

    kept: list[dict] = []
    filtered_out: list[dict] = []
    seen_urls: set[str] = set()

    for idx, raw in enumerate(items or []):
        item = dict(raw)
        url = str(item.get("url") or "").strip()
        canonical_url = ""
        if url:
            p = urlparse(url)
            canonical_url = urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", p.query, ""))
        if canonical_url and canonical_url in seen_urls:
            item["relevance_score"] = 0.0
            item["filter_reason"] = "duplicate_url"
            filtered_out.append(item)
            continue
        if canonical_url:
            seen_urls.add(canonical_url)

        score, reasons = score_research_relevance(query, item)
        item["relevance_score"] = score
        item["relevance_reasons"] = reasons
        if score >= min_relevance:
            item["_rank_order"] = idx
            kept.append(item)
        else:
            item["filter_reason"] = reasons[0] if reasons else "below_min_relevance"
            filtered_out.append(item)

    kept.sort(key=lambda x: (-float(x.get("relevance_score", 0.0)), int(x.get("_rank_order", 0))))
    for item in kept:
        item.pop("_rank_order", None)
    return kept, filtered_out


async def ingest_research(case_id: str, query: str, max_results: int = 3,
                          include_github: bool = False, plan: bool = False,
                          min_relevance: float = 0.18) -> dict:
    """Run research() and ingest every item as URL-sourced evidence (through
    case_engine.evidence, which emits the case_events). Returns ids + errors."""
    from case_engine.evidence import _create_source, ingest_evidence

    planned = None
    if plan:
        planned = await plan_queries(query, case_id=case_id)
        found = {"query": query, "items": [], "errors": []}
        seen_urls: set = set()
        per = max(1, max_results // max(1, len(planned["web"])) + 1)
        for wq in planned["web"]:
            part = await research(wq, max_results=per, include_github=False)
            found["errors"] += part["errors"]
            for it in part["items"]:
                if it["url"] not in seen_urls:
                    seen_urls.add(it["url"])
                    found["items"].append(it)
        if include_github:
            for gq in planned["github"][:2]:
                gh = await search_github(gq, max_results=3)
                if not gh.get("results") and len(gq.split()) > 1:
                    # GitHub ANDs terms; fall back to the most specific single word
                    gh = await search_github(max(gq.split(), key=len), max_results=3)
                found["errors"] += [gh["error"]] if gh.get("error") else []
                for r in gh["results"]:
                    if r["url"] in seen_urls or r.get("archived"):
                        continue
                    seen_urls.add(r["url"])
                    found["items"].append({
                        "kind": "github", "title": r["title"], "url": r["url"],
                        "content": (f"GitHub repository {r['title']} — {r['snippet']}\nstars: {r['stars']} · "
                                    f"language: {r['language']} · license: {r['license']} · last push: {r['updated']}"),
                        "stars": r["stars"], "updated": r["updated"], "archived": r["archived"],
                    })
    else:
        found = await research(query, max_results=max_results, include_github=include_github)
    kept_items, filtered_out = filter_research_items(query, found["items"], min_relevance=min_relevance)
    evidence_ids: list[str] = []
    for it in kept_items:
        if not (it.get("content") or "").strip():
            continue
        meta = {"url": it["url"], "title": it.get("title"), "web_research_query": query,
                "kind": it["kind"], "from_snippet_only": it.get("from_snippet_only", False),
                "relevance_score": it.get("relevance_score"), "planned_queries": planned}
        src = await _create_source(case_id=case_id, source_type="URL", source_uri=it["url"], metadata=meta)
        header = f"[{it['kind'].upper()}] {it.get('title') or it['url']}\n{it['url']}\n\n"
        ev = await ingest_evidence(
            case_id=case_id, content=(header + it["content"])[:MAX_TEXT_CHARS],
            source_id=src.source_id, evidence_type="DOCUMENT_CLAIM", metadata=meta,
        )
        evidence_ids.append(ev.evidence_id)
    return {"query": query, "planned": planned, "evidence_ids": evidence_ids, "items": len(kept_items),
            "filtered_out": filtered_out,
            "results": [{"kind": it["kind"], "title": it.get("title"), "url": it["url"],
                         "relevance_score": it.get("relevance_score"),
                         "snippet": (it.get("snippet") or it.get("content") or "")[:300],
                         "stars": it.get("stars"), "from_snippet_only": it.get("from_snippet_only", False)}
                        for it in kept_items],
            "errors": found["errors"]}
