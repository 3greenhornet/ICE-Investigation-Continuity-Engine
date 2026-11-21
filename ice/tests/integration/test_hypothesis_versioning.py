"""Integration tests for hypothesis versioning. Requires DB.

Verifies the append-only version semantics: every confidence change
creates a new version, prior versions are immutable, current_version_id
always points to the highest version_number.

This is the doc §5.4 complete example 2, kept verbatim except:
  * ``update_hypothesis`` keyword usage matches the implemented signature
    (hypothesis_id, new_confidence, reason, cause_event_id, ...);
  * the origin params (Part 2 §2.3) and error strings match the engine's
    actual messages ("Hypothesis with normalized_statement already
    exists in case ...", "initial_confidence must be in [0, 1]",
    "Invalid status: ...", "Invalid origin: ...").
"""
import pytest

from case_engine.case import create_case
from case_engine.hypothesis import (
    create_hypothesis,
    update_hypothesis,
    get_current_version,
    get_version_history,
    get_hypothesis,
    set_hypothesis_status,
    list_hypotheses_for_case,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_hypothesis_has_version_1(db_pool):
    case = await create_case("Test", "Test objective")
    h = await create_hypothesis(case.case_id, "Test hypothesis", 0.5)
    v1 = await get_current_version(h.hypothesis_id)
    assert v1.version_number == 1
    assert v1.confidence == 0.5
    assert v1.prior_confidence is None
    assert v1.cause_event_id.startswith("evt_")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_origin_defaults_to_user(db_pool):
    case = await create_case("Test", "Test objective")
    h = await create_hypothesis(case.case_id, "Test origin", 0.5)
    assert h.origin == "user"
    assert h.origin_detail is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_origin_external_llm_round_trips(db_pool):
    case = await create_case("Test", "Test objective")
    h = await create_hypothesis(
        case.case_id,
        "Test external",
        0.5,
        origin="external_llm",
        origin_detail="claude-3.5-sonnet, 2026-10-22",
    )
    refreshed = await get_hypothesis(h.hypothesis_id)
    assert refreshed.origin == "external_llm"
    assert refreshed.origin_detail == "claude-3.5-sonnet, 2026-10-22"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_origin_raises(db_pool):
    case = await create_case("Test", "Test objective")
    with pytest.raises(ValueError, match="Invalid origin"):
        await create_hypothesis(
            case.case_id,
            "Test invalid",
            0.5,
            origin="not_a_valid_origin",
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_creates_new_version_and_preserves_old(db_pool):
    case = await create_case("Test", "Test objective")
    h = await create_hypothesis(case.case_id, "Test", 0.5)

    v2 = await update_hypothesis(
        hypothesis_id=h.hypothesis_id,
        new_confidence=0.7,
        reason="New evidence",
        cause_event_id="evt_test",
    )
    assert v2.version_number == 2
    assert v2.prior_confidence == 0.5
    assert v2.confidence == 0.7

    history = await get_version_history(h.hypothesis_id)
    assert len(history) == 2
    assert history[0].version_number == 2      # DESC order: newest first
    assert history[0].confidence == 0.7
    assert history[1].version_number == 1
    assert history[1].confidence == 0.5        # v1 unchanged


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multiple_updates_stack_versions(db_pool):
    case = await create_case("Test", "Test objective")
    h = await create_hypothesis(case.case_id, "Stack", 0.4)

    v2 = await update_hypothesis(h.hypothesis_id, 0.5, "Step 1", "evt_1")
    v3 = await update_hypothesis(h.hypothesis_id, 0.6, "Step 2", "evt_2")
    v4 = await update_hypothesis(h.hypothesis_id, 0.7, "Step 3", "evt_3")

    assert v2.version_number == 2
    assert v3.version_number == 3
    assert v4.version_number == 4
    assert v4.confidence == 0.7
    assert v4.prior_confidence == 0.6

    history = await get_version_history(h.hypothesis_id)
    assert len(history) == 4
    assert [v.version_number for v in history] == [4, 3, 2, 1]
    assert [v.confidence for v in history] == [0.7, 0.6, 0.5, 0.4]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_hypothesis_raises(db_pool):
    case = await create_case("Test", "Test objective")
    await create_hypothesis(case.case_id, "Duplicate", 0.5)

    with pytest.raises(ValueError, match="normalized_statement already exists"):
        # Same normalized statement with punctuation difference
        await create_hypothesis(case.case_id, "Duplicate.", 0.5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_confidence_raises(db_pool):
    case = await create_case("Test", "Test objective")
    with pytest.raises(ValueError, match="initial_confidence must be in"):
        await create_hypothesis(case.case_id, "Test", 1.5)
    with pytest.raises(ValueError, match="initial_confidence must be in"):
        await create_hypothesis(case.case_id, "Test 2", -0.1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_status_transitions(db_pool):
    case = await create_case("Test", "Test objective")
    h = await create_hypothesis(case.case_id, "Test", 0.5)
    assert h.status == "PROPOSED"

    updated = await set_hypothesis_status(h.hypothesis_id, "SUPPORTED", "Evidence")
    assert updated.status == "SUPPORTED"
    assert updated.hypothesis_id == h.hypothesis_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_status_idempotent(db_pool):
    case = await create_case("Test", "Test objective")
    h = await create_hypothesis(case.case_id, "Test", 0.5)
    await set_hypothesis_status(h.hypothesis_id, "SUPPORTED", "First")
    # Second call with same status should be a no-op (returns current)
    same = await set_hypothesis_status(h.hypothesis_id, "SUPPORTED", "Second")
    assert same.status == "SUPPORTED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_status_raises(db_pool):
    case = await create_case("Test", "Test objective")
    h = await create_hypothesis(case.case_id, "Test", 0.5)
    with pytest.raises(ValueError, match="Invalid status"):
        await set_hypothesis_status(h.hypothesis_id, "NOT_A_STATUS", "reason")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_hypotheses_for_case(db_pool):
    case = await create_case("Test", "Test objective")
    await create_hypothesis(case.case_id, "H1", 0.5)
    await create_hypothesis(case.case_id, "H2", 0.5)
    await create_hypothesis(case.case_id, "H3", 0.5)

    hypotheses = await list_hypotheses_for_case(case.case_id)
    assert len(hypotheses) == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_hypotheses_filtered_by_status(db_pool):
    case = await create_case("Test", "Test objective")
    h1 = await create_hypothesis(case.case_id, "H1", 0.5)
    await create_hypothesis(case.case_id, "H2", 0.5)
    await set_hypothesis_status(h1.hypothesis_id, "SUPPORTED", "reason")

    supported = await list_hypotheses_for_case(case.case_id, status="SUPPORTED")
    assert len(supported) == 1
    assert supported[0].hypothesis_id == h1.hypothesis_id