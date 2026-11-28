"""case_engine/fingerprint.py — domain-stripped computational fingerprints.

Adapted from "Same Problem, Different Field: Cross-Domain Solution Import via
Domain-Stripped Computational Fingerprints" (Kulikowski, JCDL 2026,
arXiv:2609.07595; code Apache-2.0: github.com/ErykKul/same-problem-different-field,
src/distill_faceted.py). Their finding, which this module relies on: a paper
rewritten as a domain-free MECHANISM skeleton plus controlled structural
facets retrieves cross-field "method twins" far better than its abstract
(average precision 0.22 -> 0.56), and plain TF-IDF over the skeletons beat
four trained scientific embedders.

How ICE uses it (discovery.py): sources are gathered wide by cheap keyword
search, then every source AND the user's problem are distilled the same way;
sources are re-ranked by skeleton similarity + facet agreement, so a paper
from ecology that computes the same thing as the chatbot problem outranks a
chatbot paper that only shares vocabulary. Distillation is batched (up to 6
abstracts per LLM call) and cached on disk, so repeated discoveries get
cheaper: the index is ICE's accumulating "instinct", not a per-turn prompt.

Changes from the original: abstracts instead of full papers; 6 of their 12
facets (the availability/evidence facets need full text); a JSON batch
format instead of labeled lines; a PROBLEM variant of the prompt for the
query side. The controlled vocabularies (Berkeley 13 motifs etc.) are theirs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

FACETS = ("MECHANISM", "DOMAIN", "STRUCTURE", "DATA_OBJECT", "INFERENCE", "PROBLEM_FORM")
MATCH_FACETS = ("STRUCTURE", "DATA_OBJECT", "INFERENCE", "PROBLEM_FORM")
PROMPT_VERSION = "fp-v1"

_VOCAB = """STRUCTURE (the computational pattern; prefer one of): dense linear algebra; sparse linear algebra;
spectral or transform; N-body or all-pairs; structured grid; unstructured mesh; map-reduce or
embarrassingly-parallel; combinational logic; graph traversal; dynamic programming; backtracking or
branch-and-bound; graphical models; finite-state machine; or "other: <few words>"; or "none".
DATA_OBJECT (one of): dense matrix or tensor; sparse matrix; grid or lattice; mesh; graph or network;
point set; sequence or time-series; tree or hierarchy; set or table; continuous function or field; none.
INFERENCE (one of): deterministic or closed-form; frequentist point estimate; Bayesian posterior;
variational; sampling or Monte-Carlo; bootstrap or resampling; optimization only; none.
PROBLEM_FORM (one of): estimation; prediction or classification; optimization; decision or test; search;
counting; simulation or generation; proof or characterization; control; ranking or retrieval."""

_PAPER_PROMPT = """Reduce each research abstract below to a COMPUTATIONAL FINGERPRINT. Two papers from
different fields that use the SAME underlying computation must get the same mechanism facets even when
their topics differ.

For each item give:
- MECHANISM: 3 to 6 sentences: what is computed and the algorithmic steps in order, in GENERIC
  mathematical language. Strip ALL domain, application and dataset words (replace them with "an entity",
  "a quantity", "an observation", "an item"). Do not name famous methods unless the mathematics cannot
  be stated without the name. If the abstract has no computational method, one sentence saying so.
- DOMAIN: the topical field, 3 to 8 words (the ONLY place the field may be named).
- STRUCTURE, DATA_OBJECT, INFERENCE, PROBLEM_FORM: EXACTLY ONE lowercase value each from these lists:
""" + _VOCAB + """

Return JSON: {"fingerprints": [{"id": "<id>", "MECHANISM": "...", "DOMAIN": "...", "STRUCTURE": "...",
"DATA_OBJECT": "...", "INFERENCE": "...", "PROBLEM_FORM": "..."}]}

ABSTRACTS:
"""

_PROBLEM_PROMPT = """Reduce the problem below to the same COMPUTATIONAL FINGERPRINT used for research
papers, so it can be matched against methods from ANY field.

- MECHANISM: 3 to 6 sentences: what has to be computed or decided, the inputs, the output, and the
  constraints, in GENERIC mathematical language. Strip ALL domain, product and technology words
  ("chatbot", "user", "repository", "patient" -> "an agent", "an entity", "an item", "an observation").
  Describe the required computation, not a solution.
- DOMAIN: the field the problem comes from, 3 to 8 words.
- STRUCTURE, DATA_OBJECT, INFERENCE, PROBLEM_FORM: EXACTLY ONE lowercase value each from these lists:
""" + _VOCAB + """

Return JSON: {"MECHANISM": "...", "DOMAIN": "...", "STRUCTURE": "...", "DATA_OBJECT": "...",
"INFERENCE": "...", "PROBLEM_FORM": "..."}

PROBLEM:
"""

_STATE = Path(os.getenv("ICE_FINGERPRINT_INDEX",
                        str(Path(__file__).resolve().parent.parent / "data" / "fingerprints.json")))
_lock = threading.Lock()


def _clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().strip(".").lower() if v is not None else ""


def _normalize(fp: dict) -> dict:
    out = {f: str(fp.get(f) or "").strip() for f in FACETS}
    for f in MATCH_FACETS:
        out[f] = _clean(out[f])
    return out


def _key(work: dict) -> str:
    ident = work.get("openalex_id") or work.get("url") or work.get("title") or ""
    return hashlib.sha1(f"{PROMPT_VERSION}|{ident}".encode()).hexdigest()[:20]


class FingerprintIndex:
    """On-disk cache {key: {title, url, facets...}} with atomic writes."""

    def __init__(self, path: Path = _STATE):
        self.path = path
        self._data: Optional[dict] = None

    def _load(self) -> dict:
        if self._data is None:
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def get(self, work: dict) -> Optional[dict]:
        return self._load().get(_key(work))

    def put_many(self, items: list[tuple[dict, dict]]) -> None:
        with _lock:
            data = self._load()
            for work, fp in items:
                data[_key(work)] = {"title": work.get("title"), "url": work.get("url"), **fp}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=0, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)

    def __len__(self) -> int:
        return len(self._load())


async def _llm_json(prompt: str, op: str, case_id: Optional[str]) -> Optional[dict]:
    try:
        from case_engine.llm import _call_llm_json, llm_available

        if not llm_available():
            return None
        return await _call_llm_json(prompt=prompt, system_prompt="Return ONLY valid JSON.",
                                    temperature=0.0, role="EXTRACTOR", operation=op,
                                    prompt_id=op, prompt_version=PROMPT_VERSION,
                                    case_id=case_id, max_retries=1, timeout=90.0)
    except Exception as exc:
        logger.info("fingerprint LLM call %s failed: %s", op, exc)
        return None


async def fingerprint_problem(problem: str, case_id: Optional[str] = None) -> Optional[dict]:
    out = await _llm_json(_PROBLEM_PROMPT + problem.strip()[:3000], "fingerprint_problem", case_id)
    if not out or not str(out.get("MECHANISM") or "").strip():
        return None
    return _normalize(out)


async def fingerprint_works(works: list[dict], case_id: Optional[str] = None, batch: int = 6,
                            index: Optional[FingerprintIndex] = None) -> dict[str, dict]:
    """Fingerprints for works (cached). Returns {work key: facets}; works the
    LLM could not process are simply absent (ranked by abstract text only)."""
    index = index if index is not None else FingerprintIndex()
    out: dict[str, dict] = {}
    todo = []
    for w in works:
        hit = index.get(w)
        if hit:
            out[_key(w)] = {f: hit.get(f, "") for f in FACETS}  # same shape as fresh results
        elif (w.get("abstract") or "").strip():
            todo.append(w)
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        listing = "\n\n".join(f"[P{j + 1}] {w.get('title') or ''}\n{(w.get('abstract') or '')[:1500]}"
                              for j, w in enumerate(chunk))
        res = await _llm_json(_PAPER_PROMPT + listing, "fingerprint_papers", case_id)
        got = []
        for fp in (res or {}).get("fingerprints", []) or []:
            m = re.match(r"\[?P?(\d+)\]?", str(fp.get("id") or "").strip())
            if not m or not (1 <= int(m.group(1)) <= len(chunk)) or not str(fp.get("MECHANISM") or "").strip():
                continue
            w = chunk[int(m.group(1)) - 1]
            got.append((w, _normalize(fp)))
            out[_key(w)] = got[-1][1]
        if got:
            index.put_many(got)
    return out


def _tfidf_sims(query: str, docs: list[str]):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    try:
        vec = TfidfVectorizer(stop_words="english", sublinear_tf=True, ngram_range=(1, 2), min_df=1)
        mat = vec.fit_transform([query] + docs)
        return cosine_similarity(mat[0:1], mat[1:]).ravel()
    except ValueError:  # empty vocabulary
        return None


def _embed_sims(queries: list[str], docs: list[str]):
    """bge cosine for each query vs docs; None when the local embedder is off."""
    import os

    import numpy as np

    if os.getenv("ICE_FINGERPRINT_EMBED", "1") == "0":
        return None
    try:
        from engine.memory import EMBEDDING_BACKEND, embed_local_sync

        if EMBEDDING_BACKEND == "gemini":
            return None
        d = np.asarray(embed_local_sync([x[:800] for x in docs]), dtype=np.float32)
        q = np.asarray(embed_local_sync(queries), dtype=np.float32)
        return [d @ qi for qi in q]
    except Exception as exc:
        logger.info("fingerprint ranking without embeddings: %s", exc)
        return None


def _minmax(x):
    import numpy as np

    x = np.asarray(x, dtype=float)
    span = x.max() - x.min() if len(x) else 0.0
    return (x - x.min()) / span if span > 0 else np.zeros_like(x)


def rank_works(problem_fp: Optional[dict], problem_text: str, works: list[dict],
               fps: dict[str, dict]) -> list[dict]:
    """Score each work by combining the domain-stripped view with the original
    wording, plus 0.08 per agreeing structural facet.

    Signals (each min-max normalised, then averaged; missing ones skipped):
      * TF-IDF: problem MECHANISM vs work MECHANISM (abstract if no fingerprint)
      * TF-IDF: original problem text vs work title + abstract
      * bge:    the same two pairs, when the local embedder is available
    Why combined: on CPR-eval (benchmark/cpr_eval.py, 30-query sample) the
    stripped query alone scored Recall@10 0.30-0.37, the original alone 0.33,
    and the combination 0.43 -- the two views find different twins.
    Returns the works (copied) with ``structural_score`` and
    ``facet_matches``, best first."""
    if not works:
        return []
    stripped_q = (problem_fp or {}).get("MECHANISM") or problem_text
    raw_docs = [f"{w.get('title') or ''}. {w.get('abstract') or ''}" for w in works]
    fp_docs = [((fps.get(_key(w)) or {}).get("MECHANISM") or raw) for w, raw in zip(works, raw_docs)]
    signals = []
    for sims in (_tfidf_sims(stripped_q, fp_docs), _tfidf_sims(problem_text, raw_docs)):
        if sims is not None:
            signals.append(_minmax(sims))
    emb = _embed_sims([stripped_q, problem_text], raw_docs)
    if emb:
        signals += [_minmax(e) for e in emb]
    combined = sum(signals) / len(signals) if signals else [0.0] * len(works)
    ranked = []
    for w, sim in zip(works, combined):
        fp = fps.get(_key(w))
        matches = [f for f in MATCH_FACETS
                   if problem_fp and fp and problem_fp.get(f) and problem_fp.get(f) not in ("none", "")
                   and fp.get(f) == problem_fp.get(f)]
        ranked.append({**w, "structural_score": round(float(sim) + 0.08 * len(matches), 4),
                       "facet_matches": matches, "fingerprint": fp})
    return sorted(ranked, key=lambda r: -r["structural_score"])
