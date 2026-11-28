"""Unit tests for Semantic Scholar integration and the 8-case cross-domain discovery benchmark."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from case_engine import discovery


@pytest.mark.asyncio
async def test_semantic_scholar_census_and_field_sources_dedup(monkeypatch):
    async def fake_openalex(path, params):
        return None, "OpenAlex HTTP 429"

    async def fake_s2(text, n=5):
        return [
            {
                "title": "Maximal Marginal Relevance in Diverse Retrieval",
                "year": 1998,
                "cited_by": 2400,
                "url": "https://aclanthology.org/mmr.pdf",
                "abstract": "Balances query relevance against redundancy with already selected items.",
                "venue": "SIGIR",
                "fields_of_study": ["Information Retrieval", "Computer Science"],
                "source": "semantic_scholar",
            },
            {
                "title": "Optimal Foraging Theory in Exploratory Decision Making",
                "year": 2015,
                "cited_by": 410,
                "url": "https://arxiv.org/abs/1501.00001",
                "abstract": "Marginal value theorem determines when an agent leaves a depleting patch.",
                "venue": "Behavioral Ecology",
                "fields_of_study": ["Behavioral Ecology"],
                "source": "semantic_scholar",
            },
        ], None

    async def fake_arxiv(text, n=5):
        return [], None

    monkeypatch.setattr(discovery, "_openalex", fake_openalex)
    monkeypatch.setattr(discovery, "semantic_scholar_search", fake_s2)
    monkeypatch.setattr(discovery, "arxiv_search", fake_arxiv)

    census = await discovery.openalex_field_census(["diverse item selection under diminishing returns"])
    names = {v["name"] for v in census["subfields"].values()}
    assert "Information Retrieval" in names
    assert "Behavioral Ecology" in names

    src = await discovery.field_sources("diverse exploration", subfield_id="s2:behavioral_ecology", n=3)
    assert len(src["works"]) == 2
    assert src["works"][0]["source"] == "semantic_scholar"


def test_cross_domain_benchmark_selects_diverse_non_origin_domains(tmp_path: Path):
    bench_path = Path(__file__).resolve().parents[2] / "benchmark" / "cross_domain_cases.json"
    cases = json.loads(bench_path.read_text(encoding="utf-8"))
    assert len(cases) >= 8
    assert cases[0]["id"] == "xd_01_chatbot_repetition"

    bandit = discovery.FieldBandit(path=tmp_path / "bandit.json")
    # Seed positive feedback on cross-domain IR & Ecology so bandit reflects learned payoff too
    bandit.record("Information Retrieval", True, "MMR solved repetition")
    bandit.record("Behavioral Ecology", True, "Marginal value exploration")

    for idx, case in enumerate(cases):
        # Build candidate pool combining origin field + expected cross-domain fields + near-duplicates
        origin = case["origin_field"]
        expected = case["expected_domains"]
        candidates = [
            {
                "id": f"orig_{idx}",
                "field": f"{origin} Heuristics",
                "parent": origin,
                "hits": 25,
                "why": "obvious local pattern in the same discipline",
                "known_mechanism": "prompt tweak",
                "llm_rank": 0,
                "origin": ["llm"],
            },
            {
                "id": f"orig_dup_{idx}",
                "field": f"Advanced {origin} Prompting",
                "parent": origin,
                "hits": 22,
                "why": "another flavor from the same origin discipline",
                "known_mechanism": "few shot heuristic",
                "llm_rank": 1,
                "origin": ["llm"],
            },
        ]
        for j, dom in enumerate(expected):
            candidates.append({
                "id": f"xd_{idx}_{j}",
                "field": dom,
                "parent": dom,
                "hits": 18 - j * 2,
                "why": f"structural analog from {dom}: " + " ".join(case.get("expected_neutral_concepts", [])),
                "known_mechanism": (case.get("expected_mechanisms") or [None])[j % max(1, len(case.get("expected_mechanisms") or [""]))],
                "llm_rank": j + 2,
                "origin": ["openalex", "llm"],
            })

        picked = discovery.select_fields(
            candidates,
            k=min(4, len(candidates)),
            rng=random.Random(42 + idx),
            bandit=bandit,
            origin_field=origin,
        )
        picked_parents = {c.get("parent") or c["field"] for c in picked}
        non_origin_picked = [c for c in picked if (c.get("parent") or "") != origin]
        # Must select diverse cross-domain areas rather than collapsing into the origin field
        assert len(non_origin_picked) >= 2, f"{case['id']} collapsed to origin field: {picked}"
        assert len(picked_parents) >= 3, f"{case['id']} lacks parent diversity: {picked_parents}"
        assert picked[0]["selected_by"] == "dpp"
