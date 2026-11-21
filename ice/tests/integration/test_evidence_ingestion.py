"""Integration tests for evidence ingestion (case_engine/evidence.py). Requires DB.

Verifies the ingestion boundary end to end: row creation, type
auto-classification, the Part 1 I1 quality-score floors (MEASUREMENT 0.95)
with caller overrides, duplicate clustering, supersede chains, and the
Part 2 §2 external-LLM analysis path (MODEL_INFERENCE + source_llm +
(llm, prompt) dedup).

Engine-specific notes (verified against evidence.py):
    * sources are created via the module's ``_create_source`` — the same
      helper ingest_evidence_from_url/file and the app routes build on.
    * duplicate clustering is embedding-based; ``engine.memory.embed``
      needs a Gemini key that CI does not have, so
      ``case_engine.evidence.embed`` is monkeypatched to a deterministic
      768-dim fake (content-hash seeded). Same content -> same vector ->
      same duplicate_cluster_id; the DB round-trip is still real.
    * external-analysis dedup is exact (llm, prompt-hash) and needs no
      embedding — those tests run unpatched.
"""
import hashlib
import struct

import pytest

from case_engine import evidence as evidence_mod
from case_engine.case import create_case
from case_engine.evidence import (
    _create_source,
    get_active_evidence,
    get_evidence,
    get_stale_or_superseded,
    ingest_evidence,
    ingest_external_analysis,
    mark_superseded,
)

_case_seq = 0


async def _case():
    """Fresh case with a unique title (shared session DB)."""
    global _case_seq
    _case_seq += 1
    return await create_case(f"Evidence case {_case_seq}", "Test evidence ingestion")


async def _make_source(case_id: str):
    return await _create_source(case_id, "HUMAN", None)


@pytest.fixture
def deterministic_embeddings(monkeypatch):
    """Replace the Gemini embedder with a deterministic 768-dim fake.

    Same content -> byte-identical vector (cosine similarity 1.0, which
    clears the 0.95 duplicate threshold). Values are scaled uint32s in
    (-1, 1), so the vector is always finite — pgvector rejects NaN/Inf.
    """
    calls = []

    async def fake_embed(content: str):
        calls.append(content)
        digest = hashlib.sha256(content.strip().encode()).digest()
        words = [
            struct.unpack("<I", digest[i : i + 4])[0] for i in range(0, 32, 4)
        ]  # 8 uint32 words
        return [w / 2**31 - 1.0 for w in (words * 96)]  # 768 finite floats

    monkeypatch.setattr(evidence_mod, "embed", fake_embed)
    return calls


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_evidence_creates_row(db_pool):
    case = await _case()
    source = await _make_source(case.case_id)
    ev = await ingest_evidence(case.case_id, "the service restarted twice", source.source_id)
    assert ev.evidence_id.startswith("ev_")
    assert ev.case_id == case.case_id
    assert ev.status == "ACTIVE"
    assert ev.source_lineage_id == source.source_id  # root of its own lineage
    loaded = await get_evidence(ev.evidence_id)
    assert loaded.content == "the service restarted twice"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_evidence_auto_classifies(db_pool):
    case = await _case()
    source = await _make_source(case.case_id)
    ev = await ingest_evidence(case.case_id, "p99 latency was 250ms", source.source_id)
    assert ev.evidence_type == "MEASUREMENT"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_evidence_with_quality_score_override(db_pool):
    case = await _case()
    source = await _make_source(case.case_id)
    ev = await ingest_evidence(
        case.case_id, "p99 latency was 250ms", source.source_id, quality_score=0.42
    )
    assert ev.quality_score == 0.42


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_evidence_default_quality_score_measurement(db_pool):
    # Hari ICE improvements pass (second-pass audit: "Evidence quality
    # currently confuses what something looks like with how trustworthy it
    # is"): the MEASUREMENT type floor now applies IN FULL only to sources
    # that can produce measurements first-party. A HUMAN source reporting
    # "cpu=92" is capped at the HUMAN cap (0.90) — a person relaying a
    # number is not the instrument that measured it.
    case = await _case()
    source = await _make_source(case.case_id)  # HUMAN source
    ev = await ingest_evidence(case.case_id, "cpu=92", source.source_id)
    assert ev.quality_score == 0.90  # min(MEASUREMENT floor 0.95, HUMAN cap)

    # ...while a first-party LOG_FILE source keeps the full 0.95 floor:
    log_source = await _create_source(case.case_id, "LOG_FILE", None)
    ev2 = await ingest_evidence(case.case_id, "cpu=92", log_source.source_id)
    assert ev2.quality_score == 0.95


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_evidence_default_quality_score_observation_floor(db_pool):
    # OBSERVATION floor 0.65 also beats the default source product 0.125
    case = await _case()
    source = await _make_source(case.case_id)
    ev = await ingest_evidence(
        case.case_id, "I saw the pod restart", source.source_id
    )
    assert ev.evidence_type == "OBSERVATION"
    assert ev.quality_score == 0.65


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_evidence_quality_override_clamped(db_pool):
    case = await _case()
    source = await _make_source(case.case_id)
    ev = await ingest_evidence(
        case.case_id, "latency", source.source_id, quality_score=7.0
    )
    assert ev.quality_score == 1.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_evidence_empty_content_raises(db_pool):
    case = await _case()
    source = await _make_source(case.case_id)
    with pytest.raises(ValueError, match="content must be non-empty"):
        await ingest_evidence(case.case_id, "   ", source.source_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_detection_same_content(db_pool, deterministic_embeddings):
    case = await _case()
    source = await _make_source(case.case_id)
    first = await ingest_evidence(
        case.case_id, "the connection pool was exhausted", source.source_id
    )
    second = await ingest_evidence(
        case.case_id, "the connection pool was exhausted", source.source_id
    )
    # identical content -> identical fake embedding -> cosine similarity
    # 1.0 > 0.95 -> second joins first's duplicate cluster
    assert first.duplicate_cluster_id is not None
    assert second.duplicate_cluster_id == first.duplicate_cluster_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_distinct_content_gets_distinct_clusters(db_pool, deterministic_embeddings):
    case = await _case()
    source = await _make_source(case.case_id)
    a = await ingest_evidence(case.case_id, "cpu usage spiked", source.source_id)
    b = await ingest_evidence(case.case_id, "disk usage was flat", source.source_id)
    assert a.duplicate_cluster_id != b.duplicate_cluster_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mark_superseded_creates_chain(db_pool):
    case = await _case()
    source = await _make_source(case.case_id)
    old = await ingest_evidence(case.case_id, "latency rose", source.source_id)
    new = await ingest_evidence(case.case_id, "latency rose again", source.source_id)

    await mark_superseded(old.evidence_id, new.evidence_id, "newer reading")

    old_loaded = await get_evidence(old.evidence_id)
    new_loaded = await get_evidence(new.evidence_id)
    assert old_loaded.status == "SUPERSEDED"
    assert old_loaded.superseded_by == new.evidence_id
    assert new_loaded.supersedes == old.evidence_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_active_evidence_excludes_superseded(db_pool):
    case = await _case()
    source = await _make_source(case.case_id)
    old = await ingest_evidence(case.case_id, "latency rose", source.source_id)
    new = await ingest_evidence(case.case_id, "latency rose corrected", source.source_id)
    await mark_superseded(old.evidence_id, new.evidence_id, "newer reading")

    active_ids = [e.evidence_id for e in await get_active_evidence(case.case_id)]
    assert old.evidence_id not in active_ids
    assert new.evidence_id in active_ids

    stale = await get_stale_or_superseded(case.case_id)
    assert old.evidence_id in [e.evidence_id for e in stale]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_external_analysis_creates_model_inference(db_pool):
    case = await _case()
    ev = await ingest_external_analysis(
        case.case_id,
        llm_name="claude-3.5-sonnet",
        prompt_used="What could cause the latency regression?",
        analysis_text="The connection pool is likely exhausted.",
        conversation_context="We discussed deploys yesterday.",
        source_uri="https://claude.ai/share/x",
    )
    assert ev.evidence_type == "MODEL_INFERENCE"
    assert ev.source_llm == "claude-3.5-sonnet"
    assert ev.metadata["analyst_llm"] == "claude-3.5-sonnet"
    assert ev.metadata["prompt"] == "What could cause the latency regression?"
    assert ev.metadata["response"] == "The connection pool is likely exhausted."
    assert ev.metadata["context"] == "We discussed deploys yesterday."
    assert ev.metadata["source_uri"] == "https://claude.ai/share/x"
    assert "prompt_hash" in ev.metadata
    # the prompt + response are kept as evidence content
    assert "claude-3.5-sonnet" in ev.content
    assert "connection pool" in ev.content


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_external_analysis_dedup_same_llm_and_prompt(db_pool):
    case = await _case()
    first = await ingest_external_analysis(
        case.case_id, "gpt-4o", "Same prompt", "First answer"
    )
    second = await ingest_external_analysis(
        case.case_id, "gpt-4o", "Same prompt", "Different answer, same (llm, prompt)"
    )
    # Part 2 §2.7: exact (llm, prompt) dedup returns the EXISTING evidence
    assert second.evidence_id == first.evidence_id

    # a different prompt still ingests fresh evidence
    third = await ingest_external_analysis(
        case.case_id, "gpt-4o", "A different prompt", "Another answer"
    )
    assert third.evidence_id != first.evidence_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_external_analysis_empty_fields_raise(db_pool):
    case = await _case()
    with pytest.raises(ValueError, match="llm_name must be non-empty"):
        await ingest_external_analysis(case.case_id, "", "prompt", "answer")
    with pytest.raises(ValueError, match="prompt_used must be non-empty"):
        await ingest_external_analysis(case.case_id, "gpt-4o", "  ", "answer")
    with pytest.raises(ValueError, match="analysis_text must be non-empty"):
        await ingest_external_analysis(case.case_id, "gpt-4o", "prompt", "")