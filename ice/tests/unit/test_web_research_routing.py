"""Web research backend + code-vs-web SEARCH routing (no network: all mocked)."""
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit


def _action(target, payload=None, kind="SEARCH"):
    from case_engine.schemas import Action

    return Action(action_id="act_1", case_id="case_x", action_type=kind, target=target,
                  result_payload=payload or {}, created_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("target,expected", [
    ("latest techniques for cross-encoder reranking on CPU", "web"),
    ("alternatives to FlashRank", "web"),
    ("best practices for asyncpg connection pooling", "web"),
    ("open-source libraries for DPP diversity", "web"),
    ("where is hash_password called", "code"),
    ("login retry loop implementation", "code"),
])
def test_search_scope_by_intent(monkeypatch, target, expected):
    import case_engine.action as act
    import case_engine.repointel_client as rc

    monkeypatch.setattr(rc.RepoIntelClient, "is_available", property(lambda self: True))
    assert act.search_scope(target) == expected


def test_explicit_scope_wins_and_no_kb_means_web(monkeypatch):
    import case_engine.action as act
    import case_engine.repointel_client as rc

    monkeypatch.setattr(rc.RepoIntelClient, "is_available", property(lambda self: True))
    assert act.search_scope("latest reranker", {"scope": "code"}) == "code"
    assert act.search_scope("where is login", {"scope": "web"}) == "web"
    monkeypatch.setattr(rc.RepoIntelClient, "is_available", property(lambda self: False))
    assert act.search_scope("where is login") == "web"


def test_dispatcher_routes_search(monkeypatch):
    import case_engine.action as act
    import case_engine.repointel_client as rc

    monkeypatch.setattr(rc.RepoIntelClient, "is_available", property(lambda self: True))
    assert isinstance(act.default_executor_for(_action("who calls login")), act.RepoIntelExecutor)
    assert isinstance(act.default_executor_for(_action("best libraries for BM25 in python")), act.WebSearchExecutor)
    assert isinstance(act.default_executor_for(_action("https://example.com/a")), act.WebSearchExecutor)


async def test_research_combines_search_fetch_and_github(monkeypatch):
    import case_engine.web_research as wr

    async def fake_search(q, max_results=5):
        return {"results": [{"title": "A", "url": "https://a.test/x", "snippet": "snip a"},
                            {"title": "B", "url": "https://b.test/y", "snippet": "snip b"}], "error": None}

    async def fake_fetch(url):
        return {"url": url, "text": "full text of a" if "a.test" in url else "", "error": None}

    async def fake_gh(q, max_results=5):
        return {"results": [{"title": "o/r", "url": "https://github.com/o/r", "snippet": "desc", "stars": 10,
                             "updated": "2026-01-01", "language": "Python", "license": "MIT", "archived": False}],
                "error": None}

    monkeypatch.setattr(wr, "search_web", fake_search)
    monkeypatch.setattr(wr, "fetch_page_text", fake_fetch)
    monkeypatch.setattr(wr, "search_github", fake_gh)
    out = await wr.research("q", max_results=2, include_github=True)
    kinds = [i["kind"] for i in out["items"]]
    assert kinds == ["web", "web", "github"]
    assert out["items"][0]["content"] == "full text of a" and not out["items"][0]["from_snippet_only"]
    assert out["items"][1]["content"] == "snip b" and out["items"][1]["from_snippet_only"]
    assert "stars: 10" in out["items"][2]["content"]


async def test_web_research_can_be_disabled(monkeypatch):
    import case_engine.web_research as wr

    monkeypatch.setenv("ICE_WEB_RESEARCH", "0")
    assert (await wr.search_web("x"))["error"].startswith("web research disabled")
    assert (await wr.fetch_page_text("https://a.test"))["error"].startswith("web research disabled")


async def test_fetch_rejects_non_http():
    import case_engine.web_research as wr

    assert (await wr.fetch_page_text("file:///etc/passwd"))["error"] == "not an http(s) URL"


async def test_executor_nl_search_ingests_research(monkeypatch):
    import case_engine.action as act
    import case_engine.web_research as wr

    async def fake_ingest(case_id, query, max_results=3, include_github=False, plan=False):
        fake_ingest.called = (case_id, query, include_github, plan)
        return {"evidence_ids": ["ev_1", "ev_2"], "items": 2, "errors": []}

    monkeypatch.setattr(wr, "ingest_research", fake_ingest)
    res = await act.WebSearchExecutor().execute(_action("open-source library for reranking"))
    assert res.outcome == act.OUTCOME_EVIDENCE_PRODUCED and res.evidence_ids == ["ev_1", "ev_2"]
    assert fake_ingest.called[2] is True  # "library" -> GitHub search included


def test_distill_query_strips_lens_and_template():
    from case_engine.web_research import distill_query

    q = distill_query("[practitioner / engineering] What techniques, libraries or tools best achieve this "
                      "goal (latest best practices and alternatives): Cache the product catalog?")
    assert q == "Cache the product catalog"
    assert distill_query("who calls login") == "who calls login"


def test_github_keywords_are_short():
    from case_engine.web_research import github_keywords

    kw = github_keywords("fast CPU cross-encoder reranker ONNX alternatives to ms-marco-MiniLM-L-12")
    assert 1 <= len(kw.split()) <= 4 and "alternatives" not in kw


async def test_plan_queries_falls_back_without_llm(monkeypatch):
    import case_engine.llm as llm
    from case_engine.web_research import plan_queries

    monkeypatch.setattr(llm, "llm_available", lambda: False)
    p = await plan_queries("[x] Where in the codebase is this implemented and what currently limits it: Fast login?")
    assert p["planned_by"] == "distill" and p["web"] == ["Fast login"]


async def test_plan_queries_uses_llm_json(monkeypatch):
    import case_engine.llm as llm
    from case_engine.web_research import plan_queries

    async def fake_json(**kw):
        return {"web": ["onnx cross encoder cpu benchmark", "fast reranker"], "github": ["onnx reranker"]}

    monkeypatch.setattr(llm, "llm_available", lambda: True)
    monkeypatch.setattr(llm, "_call_llm_json", fake_json)
    p = await plan_queries("Answer in under 10 seconds with fast reranking")
    assert p["planned_by"] == "llm" and p["github"] == ["onnx reranker"] and len(p["web"]) == 2
