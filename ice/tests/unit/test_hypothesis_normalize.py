"""Unit tests for hypothesis normalization + evidence weighting.

No database (get_evidence is monkeypatched), no LLM. Covers
``_normalize_statement`` (the duplicate-hypothesis gate's normalization)
and ``_evidence_weight`` (the support-calculus weight function, including
the Part 2 §2 external-LLM distinction).

Engine surface under test (case_engine/hypothesis.py):
    _normalize_statement(statement) -> str
        lowercase -> strip non-word/non-space chars -> collapse whitespace.
    _evidence_weight(evidence_id) -> float
        MODEL_INFERENCE / MODEL_SUMMARY: 0.4 when the Evidence carries
        source_llm (external analysis, Part 2 §2), else 0.0 (ICE
        self-confirmation guard); every other type: quality_score.
        Unloadable evidence: 0.0.

    ``case_engine.hypothesis.get_evidence`` is monkeypatched with fakes so
    no DB is needed.
"""
from datetime import datetime, timezone

import pytest

from case_engine import hypothesis as hyp
from case_engine.hypothesis import _normalize_statement, _evidence_weight
from case_engine.schemas import Evidence


def _evidence(
    evidence_type: str,
    quality_score: float = 0.5,
    source_llm: str = None,
) -> Evidence:
    """Build an Evidence row shape for _evidence_weight tests."""
    now = datetime.now(timezone.utc)
    return Evidence(
        evidence_id="ev_test",
        case_id="case_test",
        source_id="src_test",
        evidence_type=evidence_type,
        content="test content",
        recorded_at=now,
        quality_score=quality_score,
        source_llm=source_llm,
    )


class TestNormalizeStatement:
    """_normalize_statement: the duplicate-hypothesis key."""

    @pytest.mark.unit
    def test_normalize_lowercases(self):
        assert _normalize_statement("The Pool Was Exhausted") == "the pool was exhausted"

    @pytest.mark.unit
    def test_normalize_strips_punctuation(self):
        assert _normalize_statement("Memory leak!") == "memory leak"
        assert _normalize_statement("cache, pool; queue") == "cache pool queue"

    @pytest.mark.unit
    def test_normalize_collapses_whitespace(self):
        assert _normalize_statement("cpu   is\nhigh") == "cpu is high"

    @pytest.mark.unit
    def test_normalize_empty_returns_empty(self):
        assert _normalize_statement("") == ""
        assert _normalize_statement("   ") == ""

    @pytest.mark.unit
    def test_normalize_equivalent_statements_collide(self):
        # The property create_hypothesis relies on for its uniqueness
        # check: punctuation/whitespace differences normalize identically.
        assert _normalize_statement("Duplicate.") == _normalize_statement("duplicate")

    @pytest.mark.unit
    def test_normalize_keeps_digits_and_underscores(self):
        # \w keeps word chars: digits and underscores survive
        assert _normalize_statement("p99_latency: 250ms") == "p99_latency 250ms"


class TestEvidenceWeight:
    """_evidence_weight: type-aware support weights (Part 2 §2)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_evidence_weight_model_inference_without_source_llm_zero(
        self, monkeypatch
    ):
        # ICE's own inference — self-confirmation guard
        async def fake_get_evidence(_eid):
            return _evidence("MODEL_INFERENCE", quality_score=0.9, source_llm=None)

        monkeypatch.setattr(hyp, "get_evidence", fake_get_evidence)
        assert await _evidence_weight("ev_test") == 0.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_evidence_weight_model_inference_with_source_llm_0_4(
        self, monkeypatch
    ):
        # External LLM analysis (Part 2 §2.1) — valuable second opinion
        async def fake_get_evidence(_eid):
            return _evidence(
                "MODEL_INFERENCE", quality_score=0.0, source_llm="claude-3.5-sonnet"
            )

        monkeypatch.setattr(hyp, "get_evidence", fake_get_evidence)
        assert await _evidence_weight("ev_test") == 0.4

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_evidence_weight_model_summary_zero(self, monkeypatch):
        # ICE's own MODEL_SUMMARY — self-confirmation guard. (A
        # MODEL_SUMMARY that carries source_llm would also score 0.4 by
        # the implemented rule "source_llm set -> 0.4", but
        # ingest_external_analysis only ever creates MODEL_INFERENCE, so
        # that combination cannot occur in practice.)
        async def fake_get_evidence(_eid):
            return _evidence("MODEL_SUMMARY", quality_score=0.9, source_llm=None)

        monkeypatch.setattr(hyp, "get_evidence", fake_get_evidence)
        assert await _evidence_weight("ev_test") == 0.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_evidence_weight_normal_returns_quality_score(self, monkeypatch):
        async def fake_get_evidence(_eid):
            return _evidence("MEASUREMENT", quality_score=0.95)

        monkeypatch.setattr(hyp, "get_evidence", fake_get_evidence)
        assert await _evidence_weight("ev_test") == 0.95

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_evidence_weight_human_assertion_returns_quality_score(
        self, monkeypatch
    ):
        async def fake_get_evidence(_eid):
            return _evidence("HUMAN_ASSERTION", quality_score=0.42)

        monkeypatch.setattr(hyp, "get_evidence", fake_get_evidence)
        assert await _evidence_weight("ev_test") == 0.42

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_evidence_weight_unloadable_evidence_zero(self, monkeypatch):
        from case_engine.exceptions import EvidenceNotFoundError

        async def fake_get_evidence(_eid):
            raise EvidenceNotFoundError("Evidence not found: ev_test")

        monkeypatch.setattr(hyp, "get_evidence", fake_get_evidence)
        assert await _evidence_weight("ev_test") == 0.0