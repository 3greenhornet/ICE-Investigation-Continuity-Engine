"""Integration tests for Case CRUD (case_engine/case.py). Requires DB.

Verifies the full case lifecycle against a real Postgres with the ICE
schema: create/get round-trip, status transitions (valid, invalid,
idempotent), listing with status/case_type filters, and the summary
count query.

Every test takes the ``db_pool`` fixture (root conftest.py) — the suite
skips cleanly when neither DATABASE_URL nor testcontainers is available.

Adaptations from the doc outline (verified against case.py):
    * each test creates its own case; list assertions use
      membership ("my case is in / out of the filtered list") rather than
      exact counts, because the shared session database accumulates rows
      from other tests.
    * summary counts additionally assert the Part 2 case_type field.
"""
from datetime import datetime, timedelta, timezone

import pytest

from case_engine.case import (
    create_case,
    get_case,
    get_case_summary,
    list_cases,
    update_case_status,
)
from case_engine.evidence import _create_source, ingest_evidence
from case_engine.hypothesis import create_hypothesis
from case_engine.exceptions import CaseNotFoundError


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_case_and_get(db_pool):
    deadline = datetime.now(timezone.utc) + timedelta(days=1)
    created = await create_case(
        "Latency regression", "Find the root cause", decision_deadline=deadline
    )
    assert created.case_id.startswith("case_")
    assert created.status == "open"
    assert created.case_type == "debug"

    loaded = await get_case(created.case_id)
    assert loaded.case_id == created.case_id
    assert loaded.title == "Latency regression"
    assert loaded.objective == "Find the root cause"
    assert loaded.decision_deadline == deadline


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_status_valid(db_pool):
    case = await create_case("Status case", "objective")
    updated = await update_case_status(case.case_id, "investigating", "work started")
    assert updated.status == "investigating"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_status_invalid_raises(db_pool):
    case = await create_case("Status case", "objective")
    with pytest.raises(ValueError, match="Invalid status"):
        await update_case_status(case.case_id, "bogus_status")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_status_idempotent(db_pool):
    case = await create_case("Status case", "objective")
    first = await update_case_status(case.case_id, "resolved", "done")
    # re-setting the same status is a no-op returning current state
    second = await update_case_status(case.case_id, "resolved", "again")
    assert second.status == "resolved"
    assert second.updated_at == first.updated_at


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_cases_returns_created(db_pool):
    case = await create_case("Listed case", "objective")
    listed = await list_cases(limit=1000)
    ids = [c.case_id for c in listed]
    assert case.case_id in ids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_cases_filtered_by_status(db_pool):
    case = await create_case("Filter status case", "objective")
    await update_case_status(case.case_id, "abandoned", "not worth it")

    abandoned = await list_cases(status="abandoned", limit=1000)
    assert case.case_id in [c.case_id for c in abandoned]

    # the case must NOT appear under a different status filter
    deciding = await list_cases(status="deciding", limit=1000)
    assert case.case_id not in [c.case_id for c in deciding]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_cases_filtered_by_case_type(db_pool):
    research_case = await create_case("Research case", "objective", case_type="research")
    debug_case = await create_case("Debug case", "objective", case_type="debug")

    research_ids = [c.case_id for c in await list_cases(case_type="research", limit=1000)]
    debug_ids = [c.case_id for c in await list_cases(case_type="debug", limit=1000)]

    assert research_case.case_id in research_ids
    assert debug_case.case_id in debug_ids
    assert research_case.case_id not in debug_ids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_case_summary_counts(db_pool):
    case = await create_case("Summary case", "objective")
    await create_hypothesis(case.case_id, "Pool exhaustion", 0.8)
    source = await _create_source(case.case_id, "HUMAN", None)
    await ingest_evidence(case.case_id, "p99 latency was 250ms", source.source_id)

    summary = await get_case_summary(case.case_id)
    assert summary["case_id"] == case.case_id
    assert summary["case_type"] == "debug"
    counts = summary["counts"]
    assert counts["evidence_active"] >= 1
    assert counts["evidence_total"] >= 1
    assert counts["hypotheses"] >= 1
    assert counts["events"] >= 3  # case_created + hypothesis_created + evidence_ingested
    assert summary["leading_hypothesis"]["statement"] == "Pool exhaustion"


# --- beyond the doc outline -----------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_case_missing_raises_not_found(db_pool):
    with pytest.raises(CaseNotFoundError):
        await get_case("case_does_not_exist_000")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_case_research_type_round_trips(db_pool):
    case = await create_case("Research round trip", "objective", case_type="research")
    loaded = await get_case(case.case_id)
    assert loaded.case_type == "research"
    # Part 2 §1.4: research cases default required_confidence to 0.0
    assert loaded.required_confidence == 0.0