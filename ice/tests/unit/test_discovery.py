"""Cross-domain discovery (case_engine.discovery). No network, no LLM, no DB:
OpenAlex / arXiv / GitHub / the LLM are all mocked."""
import random

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def bandit(tmp_path):
    from case_engine.discovery import FieldBandit

    return FieldBandit(tmp_path / "fields.json")


def _cands():
    # five computing fields that rank high + two far fields that rank lower
    rows = [("Information Retrieval", "Computer Science", 10), ("Machine Learning", "Computer Science", 9),
            ("Recommender Systems", "Computer Science", 9), ("Artificial Intelligence", "Computer Science", 8),
            ("Human-Computer Interaction", "Computer Science", 7), ("Behavioral Ecology", "Biology", 3),
            ("Marketing", "Business", 2)]
    return [{"id": f"c{i}", "field": f, "parent": p, "hits": h, "llm_rank": None, "why": ""}
            for i, (f, p, h) in enumerate(rows)]


def test_select_fields_spreads_across_areas_and_away_from_home(bandit):
    from case_engine.discovery import select_fields

    picked = select_fields(_cands(), k=3, rng=random.Random(1), bandit=bandit,
                           origin_field="Computer Science")
    parents = [p["parent"] for p in picked]
    assert "Biology" in parents and "Business" in parents      # far fields made the cut
    assert parents.count("Computer Science") <= 1              # home area discounted
    assert all(p["selected_by"] == "dpp" for p in picked)


def test_select_fields_relevance_only_baseline_stays_home(bandit):
    """Ablation: ranking by relevance alone (no DPP) picks only the home area —
    the failure the module exists to fix."""
    from case_engine.discovery import select_fields

    cands = _cands()
    select_fields(cands, k=3, rng=random.Random(1), bandit=bandit)
    top = sorted(cands, key=lambda c: -c["relevance"])[:3]
    assert {c["parent"] for c in top} == {"Computer Science"}


def test_bandit_feedback_persists_and_shifts_posterior(bandit):
    for _ in range(4):
        bandit.record("Behavioral Ecology", useful=True)
    bandit.record("Marketing", useful=False)
    stats = {s["field"]: s for s in bandit.stats()}
    assert stats["behavioral ecology"]["useful"] == 4
    assert stats["behavioral ecology"]["posterior_mean"] > 0.8
    assert stats["marketing"]["posterior_mean"] < 0.5
    rng = random.Random(0)
    draws = [bandit.draw("Behavioral Ecology", rng) for _ in range(200)]
    assert sum(draws) / len(draws) > 0.7   # proven field is drawn high on average


@pytest.mark.asyncio
async def test_cards_must_cite_given_sources(monkeypatch):
    import case_engine.discovery as d

    async def fake_llm(**kw):
        return {"cards": [
            {"name": "Marginal value theorem", "field": "Behavioral Ecology", "sources": ["S1"],
             "mechanism": "leave a patch when its rate drops below the habitat average"},
            {"name": "Invented thing", "field": "Behavioral Ecology", "sources": ["S9"]},   # no such source
            {"name": "Uncited thing", "field": "Marketing", "sources": []},
        ]}

    monkeypatch.setattr(d, "_llm", lambda *a, **k: fake_llm(**k))
    groups = [{"field": "Behavioral Ecology", "works": [
        {"title": "Optimal foraging", "year": 1976, "url": "https://doi.org/10.1/x", "abstract": "patch leaving"}]}]
    cards = await d.extract_cards("chatbot topics", {"abstract": "a"}, groups)
    assert [c["name"] for c in cards] == ["Marginal value theorem"]
    assert cards[0]["sources"][0]["url"] == "https://doi.org/10.1/x"
    assert cards[0]["adaptation_status"].startswith("proposal")


@pytest.mark.asyncio
async def test_implementations_drop_unrelated_github_hits(monkeypatch):
    import case_engine.web_research as wr
    from case_engine.discovery import attach_implementations

    async def fake_search(q, max_results=3):
        return {"error": None, "results": [
            {"title": "someone/flood-tweet-classification", "url": "u1", "snippet": "tweets", "stars": 3},
            {"title": "acme/thompson-sampling-bandits", "url": "u2", "snippet": "Thompson sampling for bandits",
             "stars": 900, "archived": False},
            {"title": "old/thompson-sampling", "url": "u3", "snippet": "", "archived": True},
        ]}

    monkeypatch.setattr(wr, "search_github", fake_search)
    cards = [{"name": "Thompson sampling topic bandit", "implementation_query": "thompson sampling"}]
    await attach_implementations(cards)
    assert [i["url"] for i in cards[0]["implementations"]] == ["u2"]


@pytest.mark.asyncio
async def test_without_llm_no_cards_only_real_leads(monkeypatch, bandit):
    """Fallback rule: no LLM -> no invented cards, the real sources come back as leads."""
    import case_engine.discovery as d

    async def no_llm(*a, **k):
        return None

    async def census(phrases):
        return {"subfields": {"1702": {"name": "Artificial Intelligence", "parent": "Computer Science",
                                       "domain": "Physical Sciences", "hits": 4, "examples": ["x"]}},
                "errors": []}

    async def sources(phrase, sid, query=None, n=4, recent=2):
        return {"works": [{"title": "A paper", "year": 2024, "url": "https://doi.org/10.2/y", "abstract": ""}],
                "errors": []}

    monkeypatch.setattr(d, "_llm", no_llm)
    monkeypatch.setattr(d, "openalex_field_census", census)
    monkeypatch.setattr(d, "field_sources", sources)
    monkeypatch.setattr(d, "FieldBandit", lambda: bandit)
    r = await d.discover("make my chatbot bring up unexpected topics", ingest=True, catalogue=True, seed=3)
    assert r["cards"] == [] and r["cards_by"] == "none"
    assert r["abstract"]["by"] == "none"
    assert r["leads"][0]["works"][0]["url"] == "https://doi.org/10.2/y"
    assert r["persisted"] == {"evidence_ids": [], "catalogued": 0}


@pytest.mark.asyncio
async def test_discover_never_raises(monkeypatch):
    import case_engine.discovery as d

    async def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(d, "abstract_problem", boom)
    r = await d.discover("some problem statement")
    assert "network down" in r["error"] and r["cards"] == []


@pytest.mark.asyncio
async def test_decision_feeds_field_bandit(monkeypatch, bandit):
    import case_engine.discovery as d
    import case_engine.mechanism_lab as ml

    async def listing(decision=None):
        return [{"mechanism_id": "mech_disc_1", "original_domain": "Behavioral Ecology"}]

    calls = []

    async def update(mid, decision, decision_note=""):
        calls.append((mid, decision))

    monkeypatch.setattr(ml, "list_mechanisms", listing)
    monkeypatch.setattr(ml, "update_mechanism_decision", update)
    monkeypatch.setattr(d, "FieldBandit", lambda: bandit)
    out = await d.decide_mechanism("mech_disc_1", "adapt", "used for topic switching")
    assert calls == [("mech_disc_1", "adapt")]
    assert out["field_posterior"]["useful"] == 1
    out = await d.decide_mechanism("mech_disc_1", "defer")
    assert "field_posterior" not in out          # defer is not a signal
    with pytest.raises(ValueError):
        await d.decide_mechanism("mech_missing", "reject")


def test_helpers():
    from case_engine.discovery import _abstract_text, _arxiv_query, _merge_candidates

    assert _abstract_text({"world": [1], "hello": [0]}) == "hello world"
    q = _arxiv_query("balancing exploration and novelty for topics")
    assert q.count("all:") == 3 and " AND " in q
    merged = _merge_candidates(
        {"subfields": {"1702": {"name": "Artificial Intelligence", "parent": "Computer Science",
                                "hits": 5, "examples": ["e"]}}},
        [{"field": "Artificial intelligence", "broad_area": "CS", "why": "w", "known_mechanism": None,
          "query": "q", "llm_rank": 0},
         {"field": "Behavioral ecology", "broad_area": "Biology", "why": "foraging", "known_mechanism": "MVT",
          "query": "patch leaving", "llm_rank": 1}])
    assert len(merged) == 2
    ai = next(m for m in merged if m["subfield_id"] == "1702")
    assert ai["origin"] == ["openalex", "llm"]   # confirmed by both sources


def test_routes_and_mcp_tools_registered():
    from app.main import app
    from app.mcp.tools_registry import TOOLS

    paths = app.openapi()["paths"]
    assert "/api/v1/discovery" in paths and "/api/v1/discovery/fields" in paths
    assert "/api/v1/discovery/mechanisms/{mechanism_id}/decision" in paths
    assert "ice_discover" in TOOLS and "ice_discovery_decision" in TOOLS
