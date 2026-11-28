"""Unit tests for ICE web research relevance scoring and noise filtering."""

from __future__ import annotations

import pytest

from case_engine import web_research


def test_filter_research_items_drops_noise_blockwalls_and_duplicates():
    query = "CrossEncoder flashrank reranker latency python"
    items = [
        {
            "kind": "web",
            "title": "Enable JavaScript and cookies to continue",
            "url": "https://example.com/blocked",
            "snippet": "Please verify you are human before viewing.",
            "content": "Please verify you are human.",
        },
        {
            "kind": "web",
            "title": "Privacy Policy - Generic Site",
            "url": "https://generic.example.org/privacy",
            "snippet": "Our privacy policy explains cookies and terms.",
            "content": "Our privacy policy explains cookies and terms.",
        },
        {
            "kind": "github",
            "title": "AnswerDotAI/rerankers",
            "url": "https://github.com/AnswerDotAI/rerankers",
            "snippet": "Unified Python API for FlashRank and CrossEncoder rerankers with low latency.",
            "content": "Unified Python API for FlashRank and CrossEncoder rerankers with low latency.",
        },
        {
            "kind": "github",
            "title": "Duplicate trailing slash",
            "url": "https://github.com/AnswerDotAI/rerankers/",
            "snippet": "Duplicate entry should be dropped.",
            "content": "Duplicate entry should be dropped.",
        },
        {
            "kind": "web",
            "title": "FlashRank ultralight CrossEncoder reranker benchmarks",
            "url": "https://pypi.org/project/flashrank/",
            "snippet": "Ultra-lite Python reranker library optimizing CrossEncoder inference latency on CPU.",
            "content": "Ultra-lite Python reranker library optimizing CrossEncoder inference latency on CPU.",
        },
    ]

    kept, filtered = web_research.filter_research_items(query, items, min_relevance=0.22)

    kept_urls = [it["url"] for it in kept]
    assert kept_urls == [
        "https://pypi.org/project/flashrank/",
        "https://github.com/AnswerDotAI/rerankers",
    ] or set(kept_urls) == {
        "https://github.com/AnswerDotAI/rerankers",
        "https://pypi.org/project/flashrank/",
    }
    assert all(it["relevance_score"] >= 0.22 for it in kept)
    reasons_by_url = {it["url"]: it.get("filter_reason") for it in filtered}
    assert reasons_by_url["https://example.com/blocked"] == "blocked_or_error_page"
    assert reasons_by_url["https://github.com/AnswerDotAI/rerankers/"] == "duplicate_url"
    assert "https://generic.example.org/privacy" in reasons_by_url


@pytest.mark.asyncio
async def test_ingest_research_only_ingests_filtered_items(monkeypatch):
    ingested_contents: list[str] = []

    async def fake_research(query, max_results=3, include_github=False):
        return {
            "items": [
                {
                    "kind": "web",
                    "title": "403 Forbidden",
                    "url": "https://bad.example.com/403",
                    "snippet": "Access denied",
                    "content": "Access denied",
                },
                {
                    "kind": "web",
                    "title": "Incremental SQLite code graph indexing with tree-sitter",
                    "url": "https://docs.python.org/3/library/sqlite3.html",
                    "snippet": "Incremental SQLite persistence and tree-sitter AST chunking.",
                    "content": "Incremental SQLite persistence and tree-sitter AST chunking.",
                },
            ],
            "errors": [],
        }

    class FakeSrc:
        source_id = "src-1"

    class FakeEv:
        evidence_id = "ev-1"

    async def fake_create_source(**kwargs):
        return FakeSrc()

    async def fake_ingest_evidence(**kwargs):
        ingested_contents.append(kwargs["content"])
        return FakeEv()

    from case_engine import evidence as ev_mod

    monkeypatch.setattr(web_research, "research", fake_research)
    monkeypatch.setattr(ev_mod, "_create_source", fake_create_source)
    monkeypatch.setattr(ev_mod, "ingest_evidence", fake_ingest_evidence)

    res = await web_research.ingest_research(
        case_id="case-123",
        query="sqlite incremental ast indexing",
        min_relevance=0.20,
    )

    assert res["evidence_ids"] == ["ev-1"]
    assert res["items"] == 1
    assert len(res["filtered_out"]) == 1
    assert len(ingested_contents) == 1
    assert "sqlite3.html" in ingested_contents[0]
