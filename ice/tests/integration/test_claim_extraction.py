"""Integration tests for claim extraction (case_engine/claim.py). Requires DB.

Verifies the evidence -> claims pipeline plumbing: LLM output shape
validation, claim row creation, extracted_claim_ids append semantics, the
claim_extracted event, and direct claim creation.

Engine-specific notes (verified against claim.py): extract_claims calls
``_call_llm_json`` (case_engine.llm) — a REAL LLM call that cannot run in
CI. The tests monkeypatch ``case_engine.claim._call_llm_json`` with a
canned response, so everything downstream of the LLM (validation, INSERT,
event emission, evidence-link append) runs against the real database.
Event payloads are jsonb and may come back as str (asyncpg codec
-dependent), so they are decoded defensively like the engine does.
"""
import json

import pytest

from case_engine import claim as claim_mod
from case_engine.case import create_case
from case_engine.claim import create_claim, extract_claims, get_claim
from case_engine.evidence import _create_source, get_evidence, ingest_evidence


async def _evidence_for(case_id: str, content: str):
    source = await _create_source(case_id, "HUMAN", None)
    return await ingest_evidence(case_id, content, source.source_id)


def _llm_returning(claims: list[dict]):
    async def fake_call_llm_json(_prompt, **_kwargs):
        return {"claims": claims}

    return fake_call_llm_json


VALID_CLAIMS = [
    {
        "statement": "p99 latency was 250ms",
        "claim_type": "MEASUREMENT",
        "scope": {"region": "us-east"},
        "valid_from": None,
        "valid_until": None,
    },
    {
        "statement": "the deploy happened at 14:00",
        "claim_type": "TEMPORAL_ASSOCIATION",
        "scope": None,
        "valid_from": "2026-09-14T14:00:00+00:00",
        "valid_until": None,
    },
]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extract_claims_returns_claims(db_pool, monkeypatch):
    case = await create_case("Claim case", "objective")
    ev = await _evidence_for(case.case_id, "p99 latency was 250ms after the deploy")

    monkeypatch.setattr(claim_mod, "_call_llm_json", _llm_returning(VALID_CLAIMS))
    claims = await extract_claims(ev.evidence_id)

    assert len(claims) == 2
    assert all(c.claim_id.startswith("c_") for c in claims)
    assert claims[0].claim_type == "MEASUREMENT"
    assert claims[0].scope == {"region": "us-east"}
    assert claims[0].status == "ACTIVE"
    # supporting_evidence_ids captured the source evidence
    assert claims[0].supporting_evidence_ids == [ev.evidence_id]
    # the ISO timestamp was parsed to an aware datetime
    assert claims[1].valid_from is not None
    assert claims[1].valid_from.tzinfo is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extract_claims_appends_to_evidence(db_pool, monkeypatch):
    case = await create_case("Claim case", "objective")
    ev = await _evidence_for(case.case_id, "I saw the pod restart")

    monkeypatch.setattr(
        claim_mod, "_call_llm_json", _llm_returning(VALID_CLAIMS[:1])
    )
    claims = await extract_claims(ev.evidence_id)

    loaded = await get_evidence(ev.evidence_id)
    assert loaded.extracted_claim_ids == [c.claim_id for c in claims]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extract_claims_emits_event(db_pool, monkeypatch):
    case = await create_case("Claim case", "objective")
    ev = await _evidence_for(case.case_id, "p99 latency was 250ms")

    async with db_pool.acquire() as conn:
        before = await conn.fetchval(
            "SELECT COUNT(*) FROM case_events WHERE case_id = $1 "
            "AND event_type = 'claim_extracted'",
            case.case_id,
        )

    monkeypatch.setattr(claim_mod, "_call_llm_json", _llm_returning(VALID_CLAIMS))
    await extract_claims(ev.evidence_id)

    async with db_pool.acquire() as conn:
        after = await conn.fetchval(
            "SELECT COUNT(*) FROM case_events WHERE case_id = $1 "
            "AND event_type = 'claim_extracted'",
            case.case_id,
        )
    assert after == before + 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extract_claims_idempotent_second_run(db_pool, monkeypatch):
    """Second run of the same extraction appends rather than overwrites.

    The engine does not dedup extracted claims (documented behavior), so
    the second run creates two NEW claim rows and array_cat-appends their
    ids — evidence.extracted_claim_ids grows, never shrinks. The doc
    outline's "may return 0 or duplicate claims" is realized as the
    append-only contract below.
    """
    case = await create_case("Claim case", "objective")
    ev = await _evidence_for(case.case_id, "p99 latency was 250ms")

    monkeypatch.setattr(claim_mod, "_call_llm_json", _llm_returning(VALID_CLAIMS))
    first = await extract_claims(ev.evidence_id)
    second = await extract_claims(ev.evidence_id)

    assert len(first) == 2
    assert len(second) == 2
    loaded = await get_evidence(ev.evidence_id)
    assert len(loaded.extracted_claim_ids) == 4  # appended, not overwritten
    assert loaded.extracted_claim_ids[:2] == [c.claim_id for c in first]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extract_claims_invalid_llm_items_skipped(db_pool, monkeypatch):
    case = await create_case("Claim case", "objective")
    ev = await _evidence_for(case.case_id, "some content")

    bad_items = [
        {"statement": "", "claim_type": "MEASUREMENT"},  # empty statement
        {"statement": "no type given", "claim_type": None},  # missing type
        {"statement": "bogus type", "claim_type": "NOT_A_TYPE"},  # invalid
        "not even a dict",
        {"statement": "valid one", "claim_type": "OBSERVATION"},  # survives
    ]
    monkeypatch.setattr(claim_mod, "_call_llm_json", _llm_returning(bad_items))
    claims = await extract_claims(ev.evidence_id)
    assert len(claims) == 1
    assert claims[0].statement == "valid one"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extract_claims_llm_unavailable_emits_empty_event(db_pool, monkeypatch):
    case = await create_case("Claim case", "objective")
    ev = await _evidence_for(case.case_id, "some content")

    async def failing_llm(_prompt, **_kwargs):
        return None  # _call_llm_json's "all models exhausted" contract

    monkeypatch.setattr(claim_mod, "_call_llm_json", failing_llm)
    claims = await extract_claims(ev.evidence_id)
    assert claims == []

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT payload FROM case_events WHERE case_id = $1 "
            "AND event_type = 'claim_extracted' ORDER BY event_seq DESC LIMIT 1",
            case.case_id,
        )
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["claim_count"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_claim_direct(db_pool):
    case = await create_case("Claim case", "objective")
    claim = await create_claim(
        case.case_id,
        "the queue depth exceeded 10k",
        "MEASUREMENT",
        scope={"service": "checkout"},
        confidence=0.7,
    )
    assert claim.claim_id.startswith("c_")
    assert claim.status == "ACTIVE"
    assert claim.claim_type == "MEASUREMENT"
    assert claim.scope == {"service": "checkout"}
    assert claim.confidence == 0.7

    loaded = await get_claim(claim.claim_id)
    assert loaded.statement == "the queue depth exceeded 10k"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_claim_invalid_inputs_raise(db_pool):
    case = await create_case("Claim case", "objective")
    with pytest.raises(ValueError, match="statement must be non-empty"):
        await create_claim(case.case_id, "  ", "MEASUREMENT")
    with pytest.raises(ValueError, match="Invalid claim_type"):
        await create_claim(case.case_id, "statement", "NOT_A_TYPE")
    with pytest.raises(ValueError, match="confidence must be in"):
        await create_claim(case.case_id, "statement", "MEASUREMENT", confidence=1.5)