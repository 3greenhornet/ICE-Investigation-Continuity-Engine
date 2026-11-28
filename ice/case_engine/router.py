"""case_engine/router.py — learned web-vs-code routing for SEARCH actions.

Replaces the keyword regex (_WEB_CUES) as the primary decision. The approach
is the "semantic router" pattern (aurelio-labs/semantic-router): each route
is a set of example utterances; a query goes to the route whose nearest
examples are most similar in embedding space. Implemented here in ~100
lines on top of ICE's local embedder (engine.memory, bge-base) instead of
adding the library, because ICE already owns the embedder and only needs
two routes.

Learning: when a SEARCH routed one way produces evidence, its text becomes
a new example of that route (record()), so routing adapts to the kinds of
questions this user actually asks. Examples persist in
ICE_ROUTER_STATE (default ice/data/search_router.json).

Fallbacks: no embedder -> None (caller uses the regex cues); a close call
(margin below ROUTER_MIN_MARGIN) -> None as well, so the router only
overrides when it is clearly more confident than a coin flip.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_STATE = Path(os.getenv("ICE_ROUTER_STATE",
                        str(Path(__file__).resolve().parent.parent / "data" / "search_router.json")))
MIN_MARGIN = float(os.getenv("ROUTER_MIN_MARGIN", "0.015"))
_CAP = 400

# Seed utterances: what each route is FOR, phrased the way questions arrive.
SEED: dict[str, list[str]] = {
    "web": [
        "latest techniques for cross-encoder reranking on CPU",
        "alternatives to FlashRank",
        "best practices for asyncpg connection pooling",
        "open-source libraries for DPP diversity",
        "how do recommender systems balance novelty and relevance",
        "marginal value theorem patch leaving in foraging animals",
        "statistical process control for detecting drift in a measurement",
        "known failure modes of exponential backoff under load",
        "papers on serendipity in information retrieval",
        "which algorithms schedule work fairly across many queues",
        "how do other fields detect anomalies in streaming signals",
        "state of the art methods for record deduplication",
        "benchmarks comparing vector databases",
        "what does the literature say about spaced repetition decay",
        "methods from ecology for exploration versus exploitation",
    ],
    "code": [
        "where is hash_password called",
        "login retry loop implementation",
        "which function builds the SQL query for findings",
        "call sites of the reranker in this repository",
        "where does the app read its configuration",
        "the module that parses uploaded files",
        "how is authentication implemented in this codebase",
        "find the class that manages the database pool",
        "which tests cover the cart total computation",
        "where are API routes for repositories defined",
        "the code path that sends notifications",
        "which file defines the goal maturity check",
        "where in the codebase is caching handled",
        "functions that call the external search API",
        "implementation of the outbox flush",
    ],
}


class SemanticRouter:
    def __init__(self, path: Path = _STATE):
        self.path = path
        self._lock = threading.Lock()
        self._vecs: dict[str, np.ndarray] = {}
        self._learned: dict[str, list[str]] = {"web": [], "code": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._learned = {r: list(data.get(r, []))[-_CAP:] for r in SEED}
        except (OSError, ValueError):
            pass

    def _embed(self, texts: list[str]) -> Optional[np.ndarray]:
        try:
            from engine.memory import EMBEDDING_BACKEND, embed_local_sync

            if EMBEDDING_BACKEND == "gemini":
                return None  # routing must not spend the remote embedding quota
            return np.asarray(embed_local_sync(texts), dtype=np.float32)
        except Exception as exc:
            logger.info("router embedder unavailable: %s", exc)
            return None

    def _route_vecs(self, route: str) -> Optional[np.ndarray]:
        examples = SEED[route] + self._learned.get(route, [])
        cached = self._vecs.get(route)
        if cached is not None and len(cached) == len(examples):
            return cached
        vecs = self._embed(examples)
        if vecs is not None:
            self._vecs[route] = vecs
        return vecs

    def scores(self, text: str) -> Optional[dict[str, float]]:
        q = self._embed([text])
        if q is None:
            return None
        out = {}
        for route in SEED:
            vecs = self._route_vecs(route)
            if vecs is None:
                return None
            sims = np.sort(vecs @ q[0])[::-1]
            out[route] = float(np.mean(sims[:3]))
        return out

    def route(self, text: str) -> tuple[Optional[str], dict]:
        sc = self.scores(text)
        if not sc:
            return None, {"reason": "no embedder"}
        best = max(sc, key=sc.get)
        margin = sc[best] - min(sc.values())
        info = {"scores": {k: round(v, 4) for k, v in sc.items()}, "margin": round(margin, 4)}
        if margin < MIN_MARGIN:
            return None, {**info, "reason": "close call"}
        return best, {**info, "reason": "semantic"}

    def record(self, text: str, route: str) -> None:
        """Learn from an outcome: text routed to `route` produced evidence."""
        text = (text or "").strip()
        if route not in SEED or not text or text in SEED[route] or text in self._learned[route]:
            return
        with self._lock:
            self._learned[route] = (self._learned[route] + [text[:300]])[-_CAP:]
            self._vecs.pop(route, None)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._learned, indent=1), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as exc:
                logger.info("router state not saved: %s", exc)


_ROUTER: Optional[SemanticRouter] = None


def get_router() -> SemanticRouter:
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = SemanticRouter()
    return _ROUTER
