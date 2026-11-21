"""Integration tests for contradiction detection (case_engine/contradiction.py).
Requires DB.

Verifies the detect_contradiction pipeline end to end: the scope/temporal
pre-LLM screens, the LLM relation gate, and the Part 2 §9 idempotency
(same pair in either order returns the SAME row, no duplicate rows, no
duplicate events).

Engine-specific note (verified against contradiction.py): the semantic
step goes through ``llm_implication_check`` -> ``_call_llm_json`` — a
real LLM call. Tests monkeypatch
``case_engine.contradiction.llm_implication_check`` with a canned
("contradiction", 0.9); the pre-checks, INSERT, ON CONFLICT handling and
event emission all run against the real database.
"""
import pytest

from case_engine import contradiction as contra_mod
from case_engine.case import create_case
from case_engine.claim import create_claim
from case_engine.contradiction import (
    detect_contradiction,
    get_active_contradictions,
    resolve_contradiction,
)
from case_engine.evidence import _create_source, ingest_evidence


async def _llm_contradiction(monkeypatch, relation="contradiction", confidence=0.9):
    async def fake_check(_a, _b):
        return relation, confidence

    monkeypatch.setattr(contra_mod, "llm_implication_check", fake_check)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detect_contradiction_opposing_claims(db_pool, monkeypatch):
    case = await create_case("Contra case", "objective")
    a = await create_claim(case.case_id, "latency fell", "MEASUREMENT")
    b = await create_claim(case.case_id, "latency rose", "MEASUREMENT")
    await _llm_contradiction(monkeypatch)

    contra = await detect_contradiction(a.claim_id, b.claim_id)
    assert contra is not None
    assert contra.contradiction_id.startswith("contra_")
    assert contra.status == "active"
    assert contra.severity == pytest.approx(0.9)
    assert contra.relation_type == "contradiction"
    # persisted and visible in the active list
    active = await get_active_contradictions(case.case_id)
    assert [c.contradiction_id for c in active] == [contra.contradiction_id]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detect_contradiction_scope_mismatch_returns_none(db_pool, monkeypatch):
    # The LLM (patched) would happily call this a contradiction; the scope
    # pre-screen must veto before any LLM call matters.
    case = await create_case("Contra case", "objective")
    a = await create_claim(
        case.case_id, "latency rose", "MEASUREMENT", scope={"region": "us-east"}
    )
    b = await create_claim(
        case.case_id, "latency fell", "MEASUREMENT", scope={"region": "eu-west"}
    )
    await _llm_contradiction(monkeypatch)

    assert await detect_contradiction(a.claim_id, b.claim_id) is None
    assert await get_active_contradictions(case.case_id) == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detect_contradiction_temporal_mismatch_returns_none(db_pool, monkeypatch):
    from datetime import datetime, timezone

    case = await create_case("Contra case", "objective")
    a = await create_claim(
        case.case_id,
        "latency rose",
        "MEASUREMENT",
        valid_from=datetime(2026, 9, 10, tzinfo=timezone.utc),
        valid_until=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    b = await create_claim(
        case.case_id,
        "latency fell",
        "MEASUREMENT",
        valid_from=datetime(2026, 9, 15, tzinfo=timezone.utc),
        valid_until=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )
    await _llm_contradiction(monkeypatch)

    assert await detect_contradiction(a.claim_id, b.claim_id) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detect_contradiction_same_claims_returns_none_or_self(
    db_pool, monkeypatch
):
    # The engine has no a==b guard; passing the same claim twice yields a
    # degenerate self-contradiction row (claim_a == claim_b). The doc
    # outline accepts "None or self" — assert it is one of the two and
    # that no crash occurs.
    case = await create_case("Contra case", "objective")
    a = await create_claim(case.case_id, "latency rose", "MEASUREMENT")
    await _llm_contradiction(monkeypatch)

    result = await detect_contradiction(a.claim_id, a.claim_id)
    assert result is None or result.claim_a == result.claim_b == a.claim_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detect_contradiction_not_a_contradiction_returns_none(
    db_pool, monkeypatch
):
    # LLM classifies the pair as independent / low confidence -> None
    case = await create_case("Contra case", "objective")
    a = await create_claim(case.case_id, "latency rose", "MEASUREMENT")
    b = await create_claim(case.case_id, "latency fell", "MEASUREMENT")
    await _llm_contradiction(monkeypatch, relation="independent", confidence=0.9)

    assert await detect_contradiction(a.claim_id, b.claim_id) is None

    await _llm_contradiction(monkeypatch, relation="contradiction", confidence=0.3)
    # below the 0.7 default threshold -> still None
    assert await detect_contradiction(a.claim_id, b.claim_id) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detect_contradiction_idempotent(db_pool, monkeypatch):
    """Part 2 §9: the same pair detected twice yields ONE row, ONE event."""
    case = await create_case("Contra case", "objective")
    a = await create_claim(case.case_id, "latency rose", "MEASUREMENT")
    b = await create_claim(case.case_id, "latency fell", "MEASUREMENT")
    await _llm_contradiction(monkeypatch)

    first = await detect_contradiction(a.claim_id, b.claim_id)
    assert first is not None

    async with db_pool.acquire() as conn:
        events_after_first = await conn.fetchval(
            "SELECT COUNT(*) FROM case_events WHERE case_id = $1 "
            "AND event_type = 'contradiction_detected'",
            case.case_id,
        )
        rows_after_first = await conn.fetchval(
            "SELECT COUNT(*) FROM contradictions WHERE case_id = $1",
            case.case_id,
        )
    assert events_after_first == 1
    assert rows_after_first == 1

    second = await detect_contradiction(a.claim_id, b.claim_id)
    assert second.contradiction_id == first.contradiction_id  # same row returned

    async with db_pool.acquire() as conn:
        events_after_second = await conn.fetchval(
            "SELECT COUNT(*) FROM case_events WHERE case_id = $1 "
            "AND event_type = 'contradiction_detected'",
            case.case_id,
        )
        rows_after_second = await conn.fetchval(
            "SELECT COUNT(*) FROM contradictions WHERE case_id = $1",
            case.case_id,
        )
    assert events_after_second == 1  # no second event
    assert rows_after_second == 1    # no second row


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detect_contradiction_reversed_order_returns_same(db_pool, monkeypatch):
    """Part 2 §9: (A, B) and (B, A) are the same contradiction."""
    case = await create_case("Contra case", "objective")
    a = await create_claim(case.case_id, "latency rose", "MEASUREMENT")
    b = await create_claim(case.case_id, "latency fell", "MEASUREMENT")
    await _llm_contradiction(monkeypatch)

    forward = await detect_contradiction(a.claim_id, b.claim_id)
    backward = await detect_contradiction(b.claim_id, a.claim_id)
    assert forward is not None
    assert backward is not None
    assert backward.contradiction_id == forward.contradiction_id

    async with db_pool.acquire() as conn:
        rows = await conn.fetchval(
            "SELECT COUNT(*) FROM contradictions WHERE case_id = $1",
            case.case_id,
        )
    assert rows == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_detect_contradiction_cross_case_raises(db_pool, monkeypatch):
    case_a = await create_case("Contra case A", "objective")
    case_b = await create_case("Contra case B", "objective")
    a = await create_claim(case_a.case_id, "latency rose", "MEASUREMENT")
    b = await create_claim(case_b.case_id, "latency fell", "MEASUREMENT")
    await _llm_contradiction(monkeypatch)

    with pytest.raises(ValueError, match="different cases"):
        await detect_contradiction(a.claim_id, b.claim_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_contradiction(db_pool, monkeypatch):
    case = await create_case("Contra case", "objective")
    a = await create_claim(case.case_id, "latency rose", "MEASUREMENT")
    b = await create_claim(case.case_id, "latency fell", "MEASUREMENT")
    await _llm_contradiction(monkeypatch)
    contra = await detect_contradiction(a.claim_id, b.claim_id)

    source = await _create_source(case.case_id, "HUMAN", None)
    ev = await ingest_evidence(case.case_id, "latency was flat", source.source_id)

    resolved = await resolve_contradiction(
        contra.contradiction_id, ev.evidence_id, "newer measurement wins"
    )
    assert resolved.status == "resolved"
    assert resolved.resolution_evidence_id == ev.evidence_id
    assert resolved.resolved_at is not None
    # resolved contradictions leave the active list
    assert await get_active_contradictions(case.case_id) == []