"""case_engine/discovery.py — cross-domain mechanism discovery.

Gap closed (CHANGES.md "Cross-domain discovery"): ICE's research step framed
every goal as "a software team's goal" and asked for two queries, so it only
ever searched the problem's own field (an agentic chatbot problem got agentic
answers, never recommender-system diversification). This module searches for
*mechanisms* wherever they were invented, without a hard-coded list of fields:

  1. abstract   — the LLM restates the problem in domain-neutral terms
                  (what has to happen, not which product it happens in).
  2. fields     — two independent sources propose fields that studied that
                  abstract problem:
                    * OpenAlex census: the neutral phrases are searched across
                      all of science; the fields of the hits are counted
                      (data, not the model's memory);
                    * the LLM names fields with a structural parallel.
  3. select     — a Thompson draw per field (Beta posterior learned from your
                  feedback) scales relevance, then DPP greedy MAP
                  (serendipity.diversity.dpp_select) picks k fields that are
                  relevant *and* different from each other.
  4. sources    — per field, OpenAlex works restricted to that field's
                  subfield (relevance + the last 3 years for freshness).
  5. cards      — one LLM call extracts mechanism cards (original mechanism /
                  transferable property / adaptation) that must cite the
                  numbered sources; cards citing nothing real are dropped.
  6. verify     — GitHub search attaches a real implementation (stars, push
                  date, license) and RepoIntel's code search reports whether
                  the repo may already contain something similar.
  7. persist    — with a case_id, each card becomes URL-sourced evidence
                  through case_engine.evidence; every card is also catalogued
                  in mechanism_lab (decision 'pending') so it survives the run.

Feedback (reuse/adapt vs reject) updates the field posterior, so fields that
produce usable mechanisms for you are drawn more often — and untried fields
still get explored (Beta(1,1) occasionally draws high).

Fallback rules: without an LLM there is no abstraction and no cards; the
report says so (``"cards_by": "none"``) and returns the real sources as leads.
Nothing is invented: no card without a cited source, no implementation link
that did not come from a GitHub search result. Never raises into callers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_OPENALEX = "https://api.openalex.org"
_TIMEOUT = float(os.getenv("ICE_DISCOVERY_TIMEOUT", "20"))
_STATE_FILE = Path(os.getenv(
    "ICE_DISCOVERY_STATE",
    str(Path(__file__).resolve().parent.parent / "data" / "discovery_fields.json")))
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 6 * 3600
_STOP = set("""a an and are as at be by can do does for from has have how i in into is it its
make me my of on or our so that the their them this to too was we what when where which while
who why will with without you your more most very just than then also about over under using use
used want need get gets new way ways""".split())


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", (text or "").lower()) if w not in _STOP}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _norm_field(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


# --------------------------------------------------------------------------- OpenAlex

async def _openalex(path: str, params: dict) -> tuple[Optional[dict], Optional[str]]:
    """GET an OpenAlex endpoint (cached 6 h; OPENALEX_API_KEY used when set).
    Returns (json, error)."""
    params = dict(params)
    key = os.getenv("OPENALEX_API_KEY")
    if key:
        params["api_key"] = key
    ck = path + json.dumps(params, sort_keys=True)
    hit = _CACHE.get(ck)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1], None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(f"{_OPENALEX}{path}", params=params)
        if resp.status_code != 200:
            return None, f"OpenAlex HTTP {resp.status_code}"
        data = resp.json()
        _CACHE[ck] = (time.monotonic(), data)
        return data, None
    except Exception as exc:
        return None, f"OpenAlex {type(exc).__name__}: {exc}"[:200]


def _abstract_text(inv: Optional[dict], limit: int = 700) -> str:
    """OpenAlex stores abstracts as an inverted index {word: [positions]}."""
    if not inv:
        return ""
    pos = sorted((p, w) for w, ps in inv.items() for p in ps)
    return " ".join(w for _, w in pos)[:limit]


def _short_id(url: Optional[str]) -> Optional[str]:
    return url.rsplit("/", 1)[-1] if url else None


# arXiv: free, keyless, no daily budget (asks for <= 1 request / 3 s). Used when
# OpenAlex is out of budget (HTTP 429) and as a second census.
_ARXIV_ARCHIVES = {
    "cs": "Computer Science", "math": "Mathematics", "stat": "Statistics", "physics": "Physics",
    "q-bio": "Quantitative Biology", "q-fin": "Quantitative Finance", "econ": "Economics",
    "eess": "Electrical Engineering and Systems Science", "nlin": "Nonlinear Sciences",
    "cond-mat": "Condensed Matter", "quant-ph": "Quantum Physics", "astro-ph": "Astrophysics",
    "gr-qc": "General Relativity", "hep-th": "High Energy Physics", "math-ph": "Mathematical Physics",
}
_arxiv_lock = asyncio.Lock()
_arxiv_last = [0.0]


def _arxiv_query(text: str, max_terms: int = 3) -> str:
    words = sorted(_tokens(text), key=lambda w: (-len(w), w))[:max_terms]
    return " AND ".join(f"all:{w}" for w in words)


async def arxiv_search(text: str, n: int = 5) -> tuple[list[dict], Optional[str]]:
    q = _arxiv_query(text)
    if not q:
        return [], "empty arXiv query"
    ck = f"arxiv|{q}|{n}"
    hit = _CACHE.get(ck)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1], None
    try:
        import httpx
        import xml.etree.ElementTree as ET

        async with _arxiv_lock:
            wait = 3.0 - (time.monotonic() - _arxiv_last[0])
            if wait > 0:
                await asyncio.sleep(wait)
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get("https://export.arxiv.org/api/query",
                                        params={"search_query": q, "max_results": n, "sortBy": "relevance"})
            _arxiv_last[0] = time.monotonic()
        if resp.status_code != 200:
            return [], f"arXiv HTTP {resp.status_code}"
        ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
        out = []
        for e in ET.fromstring(resp.text).findall("a:entry", ns):
            cat = (e.find("x:primary_category", ns).get("term") if e.find("x:primary_category", ns) is not None
                   else "")
            out.append({
                "title": re.sub(r"\s+", " ", (e.findtext("a:title", "", ns) or "")).strip(),
                "year": int((e.findtext("a:published", "", ns) or "0")[:4] or 0) or None,
                "cited_by": None, "url": (e.findtext("a:id", "", ns) or "").strip(),
                "abstract": re.sub(r"\s+", " ", e.findtext("a:summary", "", ns) or "").strip()[:700],
                "venue": f"arXiv {cat}", "category": cat,
                "archive": _ARXIV_ARCHIVES.get(cat.split(".")[0], cat.split(".")[0]),
            })
        _CACHE[ck] = (time.monotonic(), out)
        return out, None
    except Exception as exc:
        return [], f"arXiv {type(exc).__name__}: {exc}"[:200]


_SEMANTIC_SCHOLAR = "https://api.semanticscholar.org/graph/v1/paper/search"
_s2_lock = asyncio.Lock()
_s2_last = [0.0]


async def semantic_scholar_search(text: str, n: int = 5) -> tuple[list[dict], Optional[str]]:
    """Query Semantic Scholar Graph API (free keyless with polite throttling, optional API key)."""
    q = " ".join(sorted(_tokens(text), key=lambda w: (-len(w), w))[:6])
    if not q:
        return [], "empty Semantic Scholar query"
    ck = f"s2|{q}|{n}"
    hit = _CACHE.get(ck)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1], None
    headers = {}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    try:
        import httpx

        async with _s2_lock:
            wait = 1.1 - (time.monotonic() - _s2_last[0])
            if wait > 0:
                await asyncio.sleep(wait)
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(
                    _SEMANTIC_SCHOLAR,
                    params={
                        "query": q,
                        "limit": max(1, min(int(n), 20)),
                        "fields": "title,year,citationCount,url,abstract,venue,openAccessPdf,fieldsOfStudy",
                    },
                    headers=headers,
                )
            _s2_last[0] = time.monotonic()
        if resp.status_code != 200:
            return [], f"SemanticScholar HTTP {resp.status_code}"
        out = []
        for p in (resp.json() or {}).get("data", []) or []:
            title = (p.get("title") or "").strip()
            if not title:
                continue
            oa_pdf = (p.get("openAccessPdf") or {}).get("url")
            out.append({
                "title": title,
                "year": p.get("year"),
                "cited_by": p.get("citationCount"),
                "url": oa_pdf or p.get("url") or "",
                "abstract": (p.get("abstract") or "")[:700],
                "venue": p.get("venue") or "Semantic Scholar",
                "fields_of_study": list(p.get("fieldsOfStudy") or []),
                "source": "semantic_scholar",
            })
        _CACHE[ck] = (time.monotonic(), out)
        return out, None
    except Exception as exc:
        return [], f"SemanticScholar {type(exc).__name__}: {exc}"[:200]


async def openalex_field_census(phrases: list[str], per_phrase: int = 25) -> dict:
    """Search the neutral phrases across all of OpenAlex and count which
    subfields the hits belong to — a data-driven answer to "which fields
    study this?" that does not depend on the LLM's memory. Falls back to
    Semantic Scholar fieldsOfStudy and arXiv primary categories."""
    subfields: dict[str, dict] = {}
    errors: list[str] = []
    for ph in phrases[:2]:
        data, err = await _openalex("/works", {"search": ph, "per-page": per_phrase,
                                               "select": "display_name,primary_topic"})
        if err:
            errors.append(err)
            continue
        for w in (data or {}).get("results", []):
            pt = w.get("primary_topic") or {}
            sf, fld, dom = pt.get("subfield") or {}, pt.get("field") or {}, pt.get("domain") or {}
            sid = _short_id(sf.get("id"))
            if not sid:
                continue
            e = subfields.setdefault(sid, {"name": sf.get("display_name"), "parent": fld.get("display_name"),
                                           "domain": dom.get("display_name"), "hits": 0, "examples": []})
            e["hits"] += 1
            if len(e["examples"]) < 3:
                e["examples"].append(w.get("display_name"))
    if not subfields:
        for ph in phrases[:2]:
            s2_works, s2_err = await semantic_scholar_search(ph, n=12)
            if s2_err:
                errors.append(s2_err)
            for w in s2_works:
                for fos in w.get("fields_of_study") or []:
                    key = f"s2:{_norm_field(fos).replace(' ', '_')}"
                    e = subfields.setdefault(key, {
                        "name": fos, "parent": fos, "domain": None,
                        "hits": 0, "examples": [], "s2_field": fos})
                    e["hits"] += 1
                    if len(e["examples"]) < 3:
                        e["examples"].append(w["title"])
            works, err = await arxiv_search(ph, n=15)
            if err:
                errors.append(err)
            for w in works:
                cat = w["category"] or "unknown"
                e = subfields.setdefault(f"arxiv:{cat}", {
                    "name": f"{w['archive']} ({cat})", "parent": w["archive"], "domain": None,
                    "hits": 0, "examples": [], "arxiv_category": cat})
                e["hits"] += 1
                if len(e["examples"]) < 3:
                    e["examples"].append(w["title"])
    return {"subfields": subfields, "errors": errors}


async def field_sources(phrase: str, subfield_id: Optional[str], query: Optional[str] = None,
                        n: int = 4, recent: int = 2) -> dict:
    """Top works for the phrase inside one subfield (relevance), plus — when an
    OpenAlex key gives budget for it — the most relevant ones from the last 3
    years (freshness). Semantic Scholar and arXiv supplement or act as fallback."""
    select = "id,display_name,publication_year,cited_by_count,doi,abstract_inverted_index,primary_location"
    q = query or phrase
    if subfield_id and subfield_id.startswith("arxiv:"):
        works, err = await arxiv_search(q, n=n)
        return {"works": works, "errors": [err] if err else []}
    flt = f"primary_topic.subfield.id:{subfield_id}" if (subfield_id and not subfield_id.startswith("s2:")) else None
    out, errors, seen, seen_titles = [], [], set(), set()
    since = f"{datetime.now(timezone.utc).year - 3}-01-01"
    if not os.getenv("OPENALEX_API_KEY"):
        recent = 0  # the keyless daily budget is shared and small
    if not (subfield_id and subfield_id.startswith("s2:")):
        for count, extra in ((n, None), (recent, f"from_publication_date:{since}")):
            if count <= 0:
                continue
            filt = ",".join(x for x in (flt, extra) if x)
            params = {"search": q, "per-page": count, "select": select}
            if filt:
                params["filter"] = filt
            data, err = await _openalex("/works", params)
            if err:
                errors.append(err)
                continue
            for w in (data or {}).get("results", []):
                if w["id"] in seen:
                    continue
                seen.add(w["id"])
                title = (w.get("display_name") or "").strip()
                seen_titles.add(_norm_field(title))
                loc = w.get("primary_location") or {}
                out.append({
                    "title": title, "year": w.get("publication_year"),
                    "cited_by": w.get("cited_by_count"),
                    "url": w.get("doi") or loc.get("landing_page_url") or w["id"],
                    "openalex_id": w["id"], "abstract": _abstract_text(w.get("abstract_inverted_index")),
                    "venue": ((loc.get("source") or {}).get("display_name")),
                })
    if len(out) < n:
        s2_works, s2_err = await semantic_scholar_search(q, n=n)
        if s2_err:
            errors.append(s2_err)
        for w in s2_works:
            norm_t = _norm_field(w.get("title") or "")
            if norm_t and norm_t not in seen_titles:
                seen_titles.add(norm_t)
                out.append(w)
                if len(out) >= n:
                    break
    if not out:
        works, err = await arxiv_search(q, n=n)
        out = works
        errors += [err] if err else []
    return {"works": out, "errors": errors}


# --------------------------------------------------------------------------- field bandit

class FieldBandit:
    """Beta posterior per field ("did mechanisms from this field turn out
    usable?"), persisted as JSON with atomic writes. Thompson sampling over
    it is the explore/exploit dial: proven fields are drawn often, unknown
    fields still win sometimes."""

    _lock = threading.Lock()

    def __init__(self, path: Path = _STATE_FILE):
        self.path = path

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def draw(self, field: str, rng: random.Random) -> float:
        s = self.load().get(_norm_field(field), {})
        try:
            from serendipity.bandit import thompson_draw

            return thompson_draw(rng, s.get("useful", 0), s.get("not_useful", 0))
        except ImportError:
            return rng.betavariate(1 + s.get("useful", 0), 1 + s.get("not_useful", 0))

    def record(self, field: str, useful: bool, note: str = "") -> dict:
        key = _norm_field(field)
        with self._lock:
            state = self.load()
            s = state.setdefault(key, {"useful": 0, "not_useful": 0, "proposed": 0})
            s["useful" if useful else "not_useful"] += 1
            s["updated"] = datetime.now(timezone.utc).isoformat()
            if note:
                s["last_note"] = note[:200]
            self._save(state)
        return {"field": key, **s}

    def proposed(self, fields: list[str]) -> None:
        with self._lock:
            state = self.load()
            for f in fields:
                s = state.setdefault(_norm_field(f), {"useful": 0, "not_useful": 0, "proposed": 0})
                s["proposed"] = s.get("proposed", 0) + 1
            self._save(state)

    def stats(self) -> list[dict]:
        out = []
        for k, s in self.load().items():
            u, n = s.get("useful", 0), s.get("not_useful", 0)
            out.append({"field": k, **s, "posterior_mean": round((u + 1) / (u + n + 2), 3)})
        return sorted(out, key=lambda r: (-r["posterior_mean"], r["field"]))


# --------------------------------------------------------------------------- LLM steps

async def _llm(prompt: str, system: str, op: str, role: str, temperature: float,
               case_id: Optional[str]) -> Optional[dict]:
    try:
        from case_engine.llm import _call_llm_json, llm_available

        if not llm_available():
            return None
        return await _call_llm_json(prompt=prompt, system_prompt=system, temperature=temperature,
                                    role=role, operation=op, prompt_id=op, prompt_version="v1",
                                    case_id=case_id, max_retries=1, timeout=60.0)
    except Exception as exc:
        logger.info("discovery LLM step %s failed: %s", op, exc)
        return None


async def abstract_problem(problem: str, case_id: Optional[str] = None) -> dict:
    """Domain-neutral restatement + required properties + neutral search phrases."""
    out = await _llm(
        prompt=(
            "Problem as the user described it:\n"
            f"\"{problem.strip()[:1500]}\"\n\n"
            "1. Restate it as a domain-neutral functional problem: what has to happen, in terms "
            "any field could recognise (items, signals, choices, resources, time ...). Do not "
            "name the user's application, product or technology.\n"
            "2. List 3-6 properties a successful solution must have.\n"
            "3. Write 3 search phrases (3-7 words each) in that neutral vocabulary, the way "
            "researchers in different fields would phrase the underlying problem.\n"
            "4. Name the field the problem comes from (origin_field) — the one whose literature "
            "the user would search by default.\n"
            'Return JSON: {"abstract": "...", "properties": ["..."], "search_phrases": ["..."], '
            '"origin_field": "..."}'
        ),
        system="You separate a problem's structure from the domain it appeared in. Return ONLY valid JSON.",
        op="discovery_abstract", role="GENERAL", temperature=0.2, case_id=case_id)
    phrases = [p.strip() for p in (out or {}).get("search_phrases", []) if isinstance(p, str) and p.strip()]
    if out and out.get("abstract") and phrases:
        return {"abstract": str(out["abstract"]).strip(), "by": "llm", "search_phrases": phrases[:3],
                "properties": [str(p) for p in out.get("properties", []) if p][:6],
                "origin_field": str(out.get("origin_field") or "").strip() or None}
    from case_engine.web_research import distill_query

    return {"abstract": problem.strip()[:400], "by": "none", "properties": [],
            "search_phrases": [distill_query(problem)], "origin_field": None}


async def llm_fields(abstract: dict, case_id: Optional[str] = None, n: int = 12) -> list[dict]:
    out = await _llm(
        prompt=(
            f"Abstract problem: {abstract['abstract']}\n"
            f"Required properties: {'; '.join(abstract.get('properties') or []) or 'n/a'}\n\n"
            f"Name {n} distinct fields of science, engineering, industry or practice that have "
            "studied this problem or one with the same structure. Spread them as widely as the "
            "structure honestly allows; the field the problem came from may appear at most once. "
            "For each give: field, broad_area (the faculty or industry it belongs to, e.g. "
            "'Biology', 'Economics', 'Mechanical Engineering', 'Computer Science'), why "
            "(the structural parallel, one sentence), known_mechanism (a named method if you know "
            "one, else null), query (4-8 words to find it in that field's literature).\n"
            'Return JSON: {"fields": [{"field": "...", "broad_area": "...", "why": "...", '
            '"known_mechanism": null, "query": "..."}]}'
        ),
        system="You map problem structures to the fields that solved them. Return ONLY valid JSON.",
        op="discovery_fields", role="GENERATOR", temperature=0.4, case_id=case_id)
    fields = []
    for i, f in enumerate((out or {}).get("fields", []) or []):
        if isinstance(f, dict) and str(f.get("field") or "").strip():
            fields.append({"field": str(f["field"]).strip(), "broad_area": str(f.get("broad_area") or ""),
                           "why": str(f.get("why") or ""), "known_mechanism": f.get("known_mechanism"),
                           "query": str(f.get("query") or "").strip(), "llm_rank": i})
    return fields[:n]


async def extract_cards(problem: str, abstract: dict, groups: list[dict],
                        case_id: Optional[str] = None, per_field: int = 2) -> list[dict]:
    """One LLM call over all fields' numbered sources -> mechanism cards that
    must cite source ids. Cards citing no real source are dropped."""
    lines, index = [], {}
    for g in groups:
        lines.append(f"\n## Field: {g['field']}")
        for w in g["works"]:
            sid = f"S{len(index) + 1}"
            index[sid] = {**w, "field": g["field"]}
            lines.append(f"[{sid}] {w['title']} ({w.get('year')}, cited {w.get('cited_by')})"
                         f"\n    {w.get('abstract') or '(no abstract)'}")
    if not index:
        return []
    out = await _llm(
        prompt=(
            f"Original problem: \"{problem.strip()[:800]}\"\n"
            f"Abstract problem: {abstract['abstract']}\n"
            f"Sources, grouped by field:{''.join(chr(10) + ln for ln in lines)}\n\n"
            f"For each field, extract up to {per_field} concrete mechanisms (algorithm, model, "
            "control law, protocol, data structure, procedure) that the sources describe or "
            "name and that could solve the abstract problem. Skip a field whose sources describe "
            "nothing usable. Do not use knowledge that is not supported by the cited sources for "
            "the mechanism itself; the adaptation is your proposal and must be labelled as such.\n"
            "Card fields: name; field; original_problem (what its inventors were solving); "
            "mechanism (how it works, 2-4 sentences, include the core formula if a source gives "
            "it); transferable (the property that addresses the abstract problem); adaptation "
            "(concretely how to use it for the ORIGINAL problem); limitations; sources (list of "
            "ids like \"S3\"); implementation_query (2-3 keywords to find code on GitHub).\n"
            'Return JSON: {"cards": [{...}]}'
        ),
        system=("You extract reusable mechanisms from research abstracts and adapt them across "
                "domains. Cite only the given source ids. Return ONLY valid JSON."),
        op="discovery_cards", role="EXTRACTOR", temperature=0.2, case_id=case_id)
    cards = []
    for c in (out or {}).get("cards", []) or []:
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            continue
        cited = [s for s in (c.get("sources") or []) if isinstance(s, str) and s.strip() in index]
        if not cited:
            continue  # ungrounded: refuse rather than present an invented mechanism
        src = [index[s.strip()] for s in cited]
        cards.append({
            "name": str(c["name"]).strip()[:160],
            "field": str(c.get("field") or src[0]["field"]).strip(),
            "original_problem": str(c.get("original_problem") or "")[:600],
            "mechanism": str(c.get("mechanism") or "")[:1500],
            "transferable": str(c.get("transferable") or "")[:800],
            "adaptation": str(c.get("adaptation") or "")[:1200],
            "adaptation_status": "proposal (not yet tested)",
            "limitations": str(c.get("limitations") or "")[:600],
            "implementation_query": str(c.get("implementation_query") or "")[:80],
            "sources": [{"id": s.strip(), "title": x["title"], "year": x.get("year"), "url": x["url"],
                         "cited_by": x.get("cited_by")} for s, x in zip(cited, src)],
        })
    return cards


# --------------------------------------------------------------------------- selection

def select_fields(candidates: list[dict], k: int, rng: random.Random,
                  bandit: Optional[FieldBandit] = None, lambda_mult: float = 0.4,
                  origin_field: Optional[str] = None) -> list[dict]:
    """Thompson-scaled relevance + DPP greedy MAP over field similarity.

    The problem's own field is passed to the DPP as a *preselected* item: it
    is never returned, but every candidate similar to it is discounted — the
    "home field" has already been searched by default, so the set is pushed
    away from it (one close field can still win on relevance)."""
    if not candidates:
        return []
    bandit = bandit or FieldBandit()
    max_hits = max((c.get("hits", 0) for c in candidates), default=0) or 1
    n_llm = max((c["llm_rank"] for c in candidates if c.get("llm_rank") is not None), default=-1) + 1
    for c in candidates:
        census = c.get("hits", 0) / max_hits
        # LLM order is a weak signal (models list the obvious fields first)
        llm = (1.0 - 0.5 * c["llm_rank"] / max(n_llm, 1)) if c.get("llm_rank") is not None else 0.0
        base = 0.5 * census + 0.5 * llm + (0.15 if census and llm else 0.0)
        c["thompson"] = round(bandit.draw(c["field"], rng), 3)
        c["relevance"] = round(max(base, 0.05) * (0.5 + c["thompson"]), 4)
    by_id = {c["id"]: c for c in candidates}
    if origin_field:
        by_id["__origin__"] = {"id": "__origin__", "field": origin_field, "parent": origin_field, "why": ""}
    toks = {i: _tokens(" ".join([c["field"], c.get("parent") or "", c.get("why", ""),
                                 c.get("known_mechanism") or ""])) for i, c in by_id.items()}
    area = {i: _tokens(c.get("parent") or "") | _tokens(c["field"]) for i, c in by_id.items()}

    def sim(a: str, b: str) -> float:
        ca, cb = by_id[a], by_id[b]
        if _norm_field(ca["field"]) == _norm_field(cb["field"]):
            return 1.0
        same_parent = 0.7 if (ca.get("parent") and _norm_field(ca["parent"]) == _norm_field(cb.get("parent") or "")) else 0.0
        return max(same_parent, 0.8 * _jaccard(area[a], area[b]), _jaccard(toks[a], toks[b]))

    try:
        from serendipity.diversity import dpp_select

        order = dpp_select([c["id"] for c in candidates], {c["id"]: c["relevance"] for c in candidates},
                           sim, k=k, lambda_mult=lambda_mult,
                           preselected=["__origin__"] if origin_field else ())
        method = "dpp"
    except ImportError:
        order = [c["id"] for c in sorted(candidates, key=lambda c: -c["relevance"])][:k]
        method = "relevance (serendipity package missing)"
    picked = [by_id[i] for i in order]
    for c in picked:
        c["selected_by"] = method
    return picked


def _merge_candidates(census: dict, llm: list[dict]) -> list[dict]:
    """OpenAlex subfields and LLM fields in one candidate list; an LLM field
    whose name matches a subfield inherits its id (so its sources are
    field-restricted) and counts as confirmed by both."""
    cands: list[dict] = []
    by_name: dict[str, dict] = {}
    for sid, s in census.get("subfields", {}).items():
        c = {"id": f"oa:{sid}", "field": s["name"], "parent": s.get("parent"), "domain": s.get("domain"),
             "subfield_id": sid, "hits": s["hits"], "examples": s["examples"], "origin": ["openalex"],
             "why": "; ".join(s["examples"][:2]), "llm_rank": None}
        cands.append(c)
        by_name[_norm_field(s["name"])] = c
    for f in llm:
        key = _norm_field(f["field"])
        match = by_name.get(key) or next(
            (c for n, c in by_name.items() if n in key or key in n), None)
        if match:
            match["origin"].append("llm")
            match.update({"why": f["why"] or match["why"], "known_mechanism": f["known_mechanism"],
                          "query": f["query"], "llm_rank": f["llm_rank"], "broad_area": f["broad_area"]})
            continue
        c = {"id": f"llm:{len(cands)}", "field": f["field"], "parent": f["broad_area"], "subfield_id": None,
             "hits": 0, "origin": ["llm"], **{k: f[k] for k in ("why", "known_mechanism", "query", "llm_rank")}}
        cands.append(c)
        by_name[key] = c
    return cands


# --------------------------------------------------------------------------- verification

async def attach_implementations(cards: list[dict], limit: int = 6) -> list[str]:
    """Real GitHub repositories for the top cards (never an invented URL)."""
    from case_engine.web_research import search_github

    errors = []
    for c in cards[:limit]:
        q = c.get("implementation_query") or c["name"]
        res = await search_github(q, max_results=3)
        if not res.get("results") and len(q.split()) > 1:
            res = await search_github(" ".join(q.split()[:2]), max_results=3)
        if res.get("error"):
            errors.append(res["error"])
        # GitHub's search is lexical and generous: keep a repository only if its
        # name/description shares a distinctive word with the mechanism.
        want = {w for w in _tokens(f"{c['name']} {q}") if len(w) > 4}
        repos = [r for r in res.get("results", []) if not r.get("archived")
                 and want & _tokens(f"{r.get('title', '').replace('/', ' ').replace('-', ' ')} {r.get('snippet', '')}")]
        c["implementations"] = [{k: r.get(k) for k in ("title", "url", "stars", "updated", "license", "language")}
                                for r in repos[:2]]
        c["implementation_note"] = ("GitHub search results for the query; check that the repository "
                                    "implements this mechanism before using it") if repos else "none found"
    return errors


def _baseline_check_sync(cards: list[dict], repo: str) -> Optional[str]:
    try:
        from case_engine.repointel_client import RepoIntelClient

        client = RepoIntelClient(repo=repo)
        if not client.is_available:
            return "RepoIntel database not available"
        for c in cards:
            hits = client.search_code(f"{c['name']} {c.get('implementation_query') or ''}", limit=3)
            c["in_repo"] = [{"file": h.get("file_path") or h.get("file"), "symbol": h.get("name") or h.get("symbol"),
                             "score": h.get("score")} for h in hits][:3]
        return None
    except Exception as exc:
        return f"baseline check failed: {exc}"[:200]


# --------------------------------------------------------------------------- persistence

def _mechanism_id(card: dict) -> str:
    h = hashlib.sha1(f"{card['name'].lower()}|{_norm_field(card['field'])}".encode()).hexdigest()[:12]
    return f"mech_disc_{h}"


async def catalogue_cards(cards: list[dict], problem: str) -> int:
    """Insert cards into mechanism_lab (decision 'pending'); idempotent."""
    from db.connection import get_pool

    pool = await get_pool()
    if pool is None:
        return 0
    now = datetime.now(timezone.utc)
    n = 0
    async with pool.acquire() as conn:
        for c in cards:
            c["mechanism_id"] = _mechanism_id(c)
            res = await conn.execute(
                """
                INSERT INTO mechanism_lab (
                    mechanism_id, name, original_domain, original_problem, source_ref,
                    borrowed_principle, inverted_use, adaptations, experiment_status,
                    resource_profile, decision, decision_note, created_at, updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::text[],'proposed',$9::jsonb,'pending',$10,$11,$11)
                ON CONFLICT (mechanism_id) DO NOTHING
                """,
                c["mechanism_id"], c["name"], c["field"], c["original_problem"] or None,
                "; ".join(s["url"] for s in c["sources"])[:1000],
                c["transferable"] or c["mechanism"][:400], c["adaptation"] or "(no adaptation proposed)",
                [x for x in (c["limitations"],) if x],
                json.dumps({"implementations": c.get("implementations", [])}),
                f"discovered by case_engine.discovery for: {problem.strip()[:160]}", now,
            )
            n += res.endswith(" 1")
    return n


def _card_text(c: dict) -> str:
    impl = "; ".join(f"{i['title']} ({i['url']}, ★{i.get('stars')})" for i in c.get("implementations") or [])
    src = "\n".join(f"  - {s['title']} ({s.get('year')}) {s['url']}" for s in c["sources"])
    return (f"[MECHANISM] {c['name']} — from {c['field']}\n"
            f"Original problem: {c['original_problem']}\n"
            f"Mechanism: {c['mechanism']}\n"
            f"Transferable: {c['transferable']}\n"
            f"Adaptation ({c['adaptation_status']}): {c['adaptation']}\n"
            f"Limitations: {c['limitations']}\n"
            f"Implementations: {impl or 'none found'}\n"
            f"Sources:\n{src}")


async def ingest_cards(case_id: str, cards: list[dict], problem: str) -> list[str]:
    from case_engine.evidence import _create_source, ingest_evidence

    ids = []
    for c in cards:
        meta = {"kind": "mechanism_card", "field": c["field"], "mechanism": c["name"],
                "mechanism_id": c.get("mechanism_id"), "discovery_problem": problem[:300],
                "url": c["sources"][0]["url"], "card": c}
        src = await _create_source(case_id=case_id, source_type="URL", source_uri=c["sources"][0]["url"],
                                   metadata=meta)
        ev = await ingest_evidence(case_id=case_id, content=_card_text(c)[:12000], source_id=src.source_id,
                                   evidence_type="DOCUMENT_CLAIM", metadata=meta)
        ids.append(ev.evidence_id)
    return ids


# --------------------------------------------------------------------------- orchestration

async def _repo_for_case(case_id: Optional[str]) -> Optional[str]:
    if not case_id:
        return None
    try:
        from case_engine.case import get_case

        title = (await get_case(case_id)).title or ""
        return title[len("RepoIntel: "):].strip() or None if title.startswith("RepoIntel: ") else None
    except Exception:
        return None


async def discover(problem: str, case_id: Optional[str] = None, n_fields: int = 5,
                   ingest: bool = True, catalogue: bool = True, repo: Optional[str] = None,
                   seed: Optional[int] = None) -> dict:
    """Run the whole pipeline. ``ingest`` writes cards as case evidence (needs
    case_id); ``catalogue`` records them in mechanism_lab. Never raises."""
    try:
        return await _discover(problem, case_id, n_fields, ingest, catalogue, repo, seed)
    except Exception as exc:  # contract: a failed discovery is a status, not a crash
        logger.exception("discovery failed")
        return {"problem": problem, "error": f"{type(exc).__name__}: {exc}"[:300], "cards": [],
                "cards_by": "none"}


async def _discover(problem: str, case_id: Optional[str], n_fields: int, ingest: bool,
                    catalogue: bool, repo: Optional[str], seed: Optional[int]) -> dict:
    t0 = time.monotonic()
    timings: dict[str, float] = {}
    errors: list[str] = []
    rng = random.Random(seed)
    bandit = FieldBandit()
    problem = (problem or "").strip()
    if not problem:
        return {"error": "empty problem"}

    def lap(name: str) -> None:
        timings[name] = round(time.monotonic() - t0 - sum(timings.values()), 2)

    abstract = await abstract_problem(problem, case_id)
    lap("abstract")
    census, fields = await asyncio.gather(openalex_field_census(abstract["search_phrases"]),
                                          llm_fields(abstract, case_id))
    errors += census["errors"]
    lap("fields")
    candidates = _merge_candidates(census, fields)
    picked = select_fields(candidates, max(1, min(n_fields, 8)), rng, bandit,
                           origin_field=abstract.get("origin_field"))
    lap("select")

    phrase = abstract["search_phrases"][0]
    srcs = await asyncio.gather(*(field_sources(
        phrase, c.get("subfield_id"),
        query=(c.get("query") if not c.get("subfield_id") else None) or
              (phrase if c.get("subfield_id") else f"{phrase} {c['field']}"))
        for c in picked))
    groups = []
    for c, s in zip(picked, srcs):
        errors += s["errors"]
        if s["works"]:
            groups.append({"field": c["field"], "works": s["works"]})
    lap("sources")

    cards = await extract_cards(problem, abstract, groups, case_id) if abstract["by"] == "llm" else []
    lap("cards")
    if cards:
        errors += await attach_implementations(cards)
        repo = repo or await _repo_for_case(case_id)
        if repo:
            err = await asyncio.to_thread(_baseline_check_sync, cards, repo)
            if err:
                errors.append(err)
        lap("verify")
    bandit.proposed([c["field"] for c in picked])

    persisted = {"evidence_ids": [], "catalogued": 0}
    if cards and catalogue:
        try:
            persisted["catalogued"] = await catalogue_cards(cards, problem)
        except Exception as exc:
            errors.append(f"mechanism_lab: {exc}"[:200])
    if cards and ingest and case_id:
        try:
            persisted["evidence_ids"] = await ingest_cards(case_id, cards, problem)
        except Exception as exc:
            errors.append(f"evidence ingest: {exc}"[:200])
    lap("persist")

    covered = Counter(c.get("parent") or c["field"] for c in picked)
    return {
        "problem": problem, "case_id": case_id, "abstract": abstract,
        "fields_considered": [{k: c.get(k) for k in ("field", "parent", "origin", "hits", "why",
                                                    "known_mechanism", "relevance", "thompson")}
                              for c in sorted(candidates, key=lambda c: -c.get("relevance", 0))],
        "fields_selected": [{k: c.get(k) for k in ("field", "parent", "origin", "hits", "why",
                                                  "relevance", "thompson", "selected_by")} for c in picked],
        "cards": cards,
        "cards_by": "llm" if abstract["by"] == "llm" else "none",
        "leads": [] if cards else [{"field": g["field"], "works": g["works"][:3]} for g in groups],
        "diversity": {"selected": len(picked), "distinct_parent_areas": len(covered),
                      "sources_from_openalex_census": sum("openalex" in c["origin"] for c in picked)},
        "persisted": persisted, "errors": sorted(set(errors)), "timings_s": timings,
        "seconds": round(time.monotonic() - t0, 1),
        "note": ("Mechanisms come from the cited sources; each 'adaptation' is an untested proposal. "
                 "Record reuse/adapt/reject with discovery feedback so field selection learns."),
    }


async def decide_mechanism(mechanism_id: str, decision: str, note: str = "") -> dict:
    """Record a decision on a discovered mechanism and feed it to the field
    bandit: reuse/adapt = useful, reject = not useful (defer/revisit: no signal)."""
    from case_engine.mechanism_lab import list_mechanisms, update_mechanism_decision

    rows = [m for m in await list_mechanisms() if m["mechanism_id"] == mechanism_id]
    if not rows:
        raise ValueError(f"unknown mechanism_id {mechanism_id}")
    await update_mechanism_decision(mechanism_id, decision, decision_note=note)
    out = {"mechanism_id": mechanism_id, "decision": decision, "field": rows[0]["original_domain"]}
    if decision in ("reuse", "adapt", "reject"):
        out["field_posterior"] = FieldBandit().record(rows[0]["original_domain"], decision != "reject", note)
    return out
