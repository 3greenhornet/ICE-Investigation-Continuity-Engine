"""Unit tests for information-gap priority (case_engine/information_gap.py).

No database (get_gap / get_current_version are monkeypatched), no LLM.
Covers ``compute_gap_priority`` — the normalized-entropy heuristic that
ranks which missing evidence would most discriminate the case's
hypotheses.

Engine surface under test (case_engine/information_gap.py):
    compute_gap_priority(gap_id) -> float in [0.0, 1.0]
        confidences = current-version confidences of the gap's
        discriminating hypotheses, normalized to sum to 1 (planning
        beliefs); priority = H / log(n).
        Fewer than 2 resolvable hypotheses -> 0.3 (heuristic floor).
        Zero total confidence -> 0.0.
    The DB-backed loaders (get_gap, get_current_version) are monkey-
    patched in case_engine.information_gap's namespace so the pure
    arithmetic is exercised without Postgres.
"""
import math
from datetime import datetime, timezone

import pytest

from case_engine import information_gap as gap_mod
from case_engine.information_gap import compute_gap_priority
from case_engine.schemas import InformationGap, HypothesisVersion


_NOW = datetime.now(timezone.utc)


def _gap(hypothesis_ids: list[str]) -> InformationGap:
    return InformationGap(
        gap_id="gap_test",
        case_id="case_test",
        needed_evidence="needed",
        why_needed="why",
        discriminating_hypothesis_ids=hypothesis_ids,
        created_at=_NOW,
    )


def _version(confidence: float) -> HypothesisVersion:
    return HypothesisVersion(
        version_id=f"hv_{confidence}",
        hypothesis_id="h_test",
        version_number=1,
        confidence=confidence,
        reason="test",
        cause_event_id="evt_test",
        created_at=_NOW,
    )


def _patch_loaders(monkeypatch, confidences: list[float], gap=None):
    """Point information_gap's loaders at in-memory fakes."""
    conf_by_id = {f"h_{i}": c for i, c in enumerate(confidences)}

    async def fake_get_gap(_gap_id):
        return gap if gap is not None else _gap(list(conf_by_id.keys()))

    async def fake_get_current_version(hypothesis_id):
        if hypothesis_id not in conf_by_id:
            from case_engine.exceptions import HypothesisNotFoundError

            raise HypothesisNotFoundError(hypothesis_id)
        return _version(conf_by_id[hypothesis_id])

    monkeypatch.setattr(gap_mod, "get_gap", fake_get_gap)
    monkeypatch.setattr(gap_mod, "get_current_version", fake_get_current_version)


class TestComputeGapPriority:
    """Normalized-entropy priority over discriminating hypotheses."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_priority_two_hypotheses_uniform(self, monkeypatch):
        _patch_loaders(monkeypatch, [0.5, 0.5])
        assert await compute_gap_priority("gap_test") == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_priority_two_hypotheses_skewed(self, monkeypatch):
        _patch_loaders(monkeypatch, [0.9, 0.1])
        expected = (
            -(0.9 * math.log(0.9) + 0.1 * math.log(0.1)) / math.log(2)
        )
        assert expected == pytest.approx(0.469, abs=1e-2)  # the doc's "~0.47"
        assert await compute_gap_priority("gap_test") == pytest.approx(
            expected, abs=1e-6
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_priority_three_hypotheses_uniform(self, monkeypatch):
        _patch_loaders(monkeypatch, [1 / 3, 1 / 3, 1 / 3])
        assert await compute_gap_priority("gap_test") == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_priority_single_hypothesis_returns_0_3(self, monkeypatch):
        _patch_loaders(monkeypatch, [0.7])
        assert await compute_gap_priority("gap_test") == 0.3

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_priority_no_hypotheses_returns_0_3(self, monkeypatch):
        _patch_loaders(monkeypatch, [])
        assert await compute_gap_priority("gap_test") == 0.3

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_priority_zero_total_returns_0_0(self, monkeypatch):
        _patch_loaders(monkeypatch, [0.0, 0.0])
        assert await compute_gap_priority("gap_test") == 0.0

    # --- beyond the doc outline ----------------------------------------

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_priority_missing_hypothesis_falls_back_to_0_3(self, monkeypatch):
        # One of the two discriminating hypotheses no longer loads ->
        # fewer than 2 confidences -> the 0.3 floor.
        async def fake_get_gap(_gap_id):
            return _gap(["h_known", "h_missing"])

        async def fake_get_current_version(hypothesis_id):
            if hypothesis_id == "h_known":
                return _version(0.6)
            from case_engine.exceptions import HypothesisNotFoundError

            raise HypothesisNotFoundError(hypothesis_id)

        monkeypatch.setattr(gap_mod, "get_gap", fake_get_gap)
        monkeypatch.setattr(gap_mod, "get_current_version", fake_get_current_version)
        assert await compute_gap_priority("gap_test") == 0.3

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_priority_is_bounded_0_1(self, monkeypatch):
        _patch_loaders(monkeypatch, [0.5, 0.5])
        value = await compute_gap_priority("gap_test")
        assert 0.0 <= value <= 1.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_priority_uniform_beats_skewed(self, monkeypatch):
        """A balanced hypothesis set is a higher-priority gap than a
        nearly-decided one — the entire point of entropy-based priority."""
        _patch_loaders(monkeypatch, [0.5, 0.5])
        uniform = await compute_gap_priority("gap_test")
        _patch_loaders(monkeypatch, [0.95, 0.05])
        skewed = await compute_gap_priority("gap_test")
        assert uniform > skewed