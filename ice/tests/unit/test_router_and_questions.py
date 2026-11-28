"""Learned SEARCH routing (case_engine.router) and field-based research
questions (research._cross_domain_questions). No network, no real model:
a bag-of-words embedder stands in for bge."""
import random

import numpy as np
import pytest

pytestmark = pytest.mark.unit

VOCAB = ["where", "codebase", "function", "module", "file", "implemented", "calls", "our",
         "techniques", "papers", "fields", "literature", "approaches", "theory", "alternatives",
         "foraging", "ecology", "queueing", "test"]


def _bow(texts):
    out = []
    for t in texts:
        v = np.array([t.lower().count(w) for w in VOCAB], dtype=np.float32) + 0.01
        out.append(v / np.linalg.norm(v))
    return np.stack(out)


@pytest.fixture
def router(tmp_path, monkeypatch):
    from case_engine import router as r

    # toy seeds: this tests the routing mechanics, not the real seed set
    monkeypatch.setattr(r, "SEED", {"web": ["papers literature techniques", "approaches theory fields"],
                                    "code": ["where codebase function implemented", "module file calls our"]})
    rt = r.SemanticRouter(tmp_path / "router.json")
    monkeypatch.setattr(rt, "_embed", lambda texts: _bow(texts))
    return rt


def test_routes_by_similarity(router):
    assert router.route("where in our codebase is the function implemented")[0] == "code"
    assert router.route("papers and literature on approaches and techniques from other fields")[0] == "web"


def test_close_call_defers_to_fallback(router, monkeypatch):
    monkeypatch.setattr(router, "scores", lambda text: {"web": 0.50, "code": 0.495})
    route, info = router.route("ambiguous")
    assert route is None and info["reason"] == "close call"


def test_learning_adds_examples_and_persists(router, tmp_path, monkeypatch):
    from case_engine import router as r

    router.record("foraging ecology patch leaving", "web")
    router.record("foraging ecology patch leaving", "web")      # no duplicates
    again = r.SemanticRouter(tmp_path / "router.json")
    assert again._learned["web"] == ["foraging ecology patch leaving"]
    router.record("x", "nonsense-route")                         # ignored
    assert "nonsense-route" not in again._learned


def test_search_scope_uses_router_then_regex(monkeypatch):
    import case_engine.action as act
    import case_engine.repointel_client as rc
    import case_engine.router as r

    monkeypatch.setattr(rc.RepoIntelClient, "is_available", property(lambda self: True))
    monkeypatch.setenv("ICE_SEMANTIC_ROUTER", "1")

    class Fixed:
        def __init__(self, ans):
            self.ans = ans

        def route(self, text):
            return self.ans, {"reason": "semantic"}

    monkeypatch.setattr(r, "get_router", lambda: Fixed("web"))
    assert act.search_scope_explain("how do ecologists model foraging")["scope"] == "web"
    monkeypatch.setattr(r, "get_router", lambda: Fixed(None))     # no confident answer
    out = act.search_scope_explain("latest alternatives to FlashRank")
    assert out == {"scope": "web", "reason": "keyword cues (fallback)"}
    assert act.search_scope_explain("x", {"scope": "code"})["reason"] == "explicit"


@pytest.mark.asyncio
async def test_cross_domain_questions_use_selected_fields(monkeypatch):
    import case_engine.discovery as d
    from case_engine import research

    async def abstract(goal, case_id=None):
        return {"abstract": "select items balancing value and novelty", "by": "llm",
                "search_phrases": ["novelty relevance trade-off"], "origin_field": "Conversational AI"}

    async def census(phrases):
        return {"subfields": {}, "errors": []}

    async def fields(abs_, case_id=None, n=12):
        return [{"field": "Behavioral Ecology", "broad_area": "Biology", "why": "patch leaving",
                 "known_mechanism": "marginal value theorem", "query": "patch leaving marginal value", "llm_rank": 0},
                {"field": "Conversational AI", "broad_area": "Computer Science", "why": "same field",
                 "known_mechanism": None, "query": "chatbot topic diversity", "llm_rank": 1},
                {"field": "Marketing", "broad_area": "Business", "why": "variety seeking",
                 "known_mechanism": None, "query": "consumer variety seeking", "llm_rank": 2}]

    monkeypatch.setattr(d, "abstract_problem", abstract)
    monkeypatch.setattr(d, "openalex_field_census", census)
    monkeypatch.setattr(d, "llm_fields", fields)
    monkeypatch.setattr(d.FieldBandit, "draw", lambda self, f, rng: 0.5)
    qs = await research._cross_domain_questions("make the chatbot less repetitive", "case_x", k=2)
    picked = [q["field"] for q in qs]
    assert "Conversational AI" not in picked                      # home field discounted by the DPP
    assert qs[0]["question"].startswith("[") and "]" in qs[0]["question"]


@pytest.mark.asyncio
async def test_cross_domain_questions_empty_without_llm(monkeypatch):
    import case_engine.discovery as d
    from case_engine import research

    async def abstract(goal, case_id=None):
        return {"abstract": goal, "by": "none", "search_phrases": [goal], "origin_field": None}

    monkeypatch.setattr(d, "abstract_problem", abstract)
    assert await research._cross_domain_questions("anything", "case_x") == []


# --------------------------------------------------------------------------- stall trigger

@pytest.mark.parametrize("cur,prev,maturity,status,level,mode,last,expected", [
    ("unchanged", "unchanged", "candidate", "active", 1, "auto", None, True),
    ("regressed", "unchanged", "connected", "active", 1, "auto", None, True),
    ("improved", "unchanged", "candidate", "active", 1, "auto", None, False),   # progressing
    ("unchanged", None, "candidate", "active", 1, "auto", None, False),         # one check is not a stall
    ("unchanged", "unchanged", "verified", "active", 1, "auto", None, False),   # already done
    ("unchanged", "unchanged", "candidate", "abandoned", 1, "auto", None, False),
    ("unchanged", "unchanged", "candidate", "active", 0, "auto", None, False),  # passive dial
    ("unchanged", "unchanged", "candidate", "active", 0, "force", None, True),
    ("unchanged", "unchanged", "candidate", "active", 1, "off", None, False),
    ("unchanged", "unchanged", "candidate", "active", 1, "auto", 1000.0, False),  # cooldown
])
def test_auto_discovery_decision(monkeypatch, cur, prev, maturity, status, level, mode, last, expected):
    import case_engine.action as act
    from case_engine.goals import auto_discovery_decision

    monkeypatch.setattr(act, "AUTONOMY_LEVEL", level)
    monkeypatch.setenv("ICE_AUTO_DISCOVERY", mode)
    run, why = auto_discovery_decision({"status": status}, cur, prev, maturity, now=2000.0, last_run=last)
    assert run is expected, why


def test_prev_progress_status_parsing():
    from case_engine.goals import _prev_progress_status

    assert _prev_progress_status({"note": "auto from RepoIntel (r): [unchanged; 2/5 checks] x"}) == "unchanged"
    assert _prev_progress_status({"note": "manual"}) is None
    assert _prev_progress_status(None) is None
