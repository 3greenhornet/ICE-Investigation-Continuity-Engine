"""Integration tests for research mode (case_engine/research.py).

The contracts from the improvements document (section G — research must
complete the research PROCESS):

    * PERSPECTIVE discovery returns lenses beyond the obvious ones,
      derived from the actual claim-type / evidence mix, and marks the
      UNEXPLORED ones (STORM's insight: questions should come from
      angles the current evidence does not speak to);
    * RESEARCH QUESTIONS that evidence does not answer become REAL
      information gaps (wired into the existing gap machinery), are
      deduplicated across runs, and never auto-execute anything;
    * SYNTHESIS OUTLINE separates epistemic tiers — established /
      contested / interpreted — keeps citations, and reports the
      marginal-value signal instead of deciding it;
    * the BRIEF runs the whole pass in one call.
"""
import pytest

from case_engine.case import create_case
from case_engine.claim import create_claim
from case_engine.evidence import _create_source, ingest_evidence
from case_engine.information_gap import list_gaps_for_case
from case_engine.research import (
    discover_perspectives, generate_research_questions,
    research_brief, research_synthesis_outline,
)


async def _seeded_case():
    case = await create_case(
        "Vector databases for personal memory",
        "Decide whether a vector database is needed",
        case_type="research",
    )
    src = await _create_source(case.case_id, "HUMAN", None)
    await ingest_evidence(
        case.case_id,
        "pgvector provides approximate nearest neighbour search on "
        "postgres; hybrid search combines full text and vector channels",
        src.source_id, "HUMAN_ASSERTION",
    )
    await create_claim(
        case.case_id,
        "Hybrid search outperforms pure vector search on recall",
        claim_type="MEASUREMENT",
    )
    await create_claim(
        case.case_id,
        "Vector databases are probably the right default for memory",
        claim_type="INTERPRETATION",
    )
    return case


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discover_perspectives_marks_unexplored(db_pool):
    case = await _seeded_case()
    disc = await discover_perspectives(case.case_id)
    names = {p["perspective"] for p in disc["perspectives"]}
    # derived from the claim-type mix actually present…
    assert "empirical / quantitative" in names
    # …plus the default lenses nobody had to think of
    assert "skeptical / critic" in names
    assert "stakeholder / user-impact" in names
    assert disc["summary"]["total"] >= len(names)
    # grounded counting works: the empirical lens sees the MEASUREMENT claim
    emp = next(p for p in disc["perspectives"]
               if p["perspective"] == "empirical / quantitative")
    assert emp["grounded_claims"] >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_questions_become_real_gaps_and_dedupe(db_pool):
    case = await _seeded_case()
    before = await list_gaps_for_case(case.case_id)
    result = await generate_research_questions(case.case_id, max_gaps=6)
    assert result["gaps_created"], "unexplored perspectives must yield questions"

    after = await list_gaps_for_case(case.case_id)
    assert len(after) == len(before) + len(result["gaps_created"])
    created_texts = {g["question"] for g in result["gaps_created"]}
    for gap in after:
        if any(cid == gap.gap_id for cid in
               (g["gap_id"] for g in result["gaps_created"])):
            assert gap.why_needed.startswith(
                "Perspective-guided research question"
            )

    # second run: no duplicates
    result2 = await generate_research_questions(case.case_id, max_gaps=6)
    ids1 = {g["gap_id"] for g in result["gaps_created"]}
    ids2 = {g["gap_id"] for g in result2["gaps_created"]}
    assert not (ids1 & ids2)
    final = await list_gaps_for_case(case.case_id)
    assert len(final) == len(before) + len(result["gaps_created"]) + len(
        result2["gaps_created"]
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_synthesis_outline_separates_tiers(db_pool):
    case = await _seeded_case()
    await create_claim(
        case.case_id,
        "pgvector recall drops sharply above ten million rows",
        claim_type="OBSERVATION",
    )
    outline = await research_synthesis_outline(case.case_id)
    # the INTERPRETATION claim never renders as established
    kinds = {c["claim_type"] for c in outline["established"]}
    assert "INTERPRETATION" not in kinds
    assert any(c["claim_type"] == "INTERPRETATION"
               for c in outline["interpreted"])
    # measurement + hybrid-search claims with no contradiction -> established
    assert any("Hybrid search" in c["statement"]
               for c in outline["established"])
    assert outline["summary"]["established"] >= 1
    assert "marginal_value_signal" in outline["summary"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_research_brief_end_to_end(db_pool):
    case = await _seeded_case()
    brief = await research_brief(case.case_id, max_gaps=3)
    assert set(brief.keys()) >= {"perspectives", "questions", "synthesis"}
    assert brief["perspectives"]["summary"]["total"] > 0
    assert brief["questions"]["gaps_created"]
    assert brief["synthesis"]["summary"]["established"] >= 1
