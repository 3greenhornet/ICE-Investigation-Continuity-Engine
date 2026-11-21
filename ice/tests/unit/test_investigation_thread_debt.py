"""Unit tests for investigation-thread debt (case_engine/investigation_thread.py).

No database, no LLM. Covers ``InvestigationThreadRegistry.compute_debt`` —
the formula that surfaces neglected investigation threads to the
next-best-action scorer (Part 2 §8).

Engine surface under test (case_engine/investigation_thread.py):
    InvestigationThreadRegistry(case_id).compute_debt(thread) -> float
        debt = strength x min(age_days, 30) x decision_impact x uncertainty
        age_days = (now - last_progress_at) / 86400, floored at 0.01 days
        and capped at MAX_AGE_CAP_DAYS = 30.
    The registry constructor touches no I/O and compute_debt is pure
    arithmetic, so no pool is needed.
"""
from datetime import datetime, timedelta, timezone

import pytest

from case_engine.investigation_thread import (
    InvestigationThreadRegistry,
    MAX_AGE_CAP_DAYS,
)
from case_engine.schemas import InvestigationThread

_NOW = datetime.now(timezone.utc)


def _thread(
    strength: float = 0.5,
    age_days: float = 0.0,
    decision_impact: float = 0.5,
    uncertainty: float = 0.5,
    question: str = "Why did latency regress?",
) -> InvestigationThread:
    return InvestigationThread(
        thread_id="thread_test",
        case_id="case_test",
        question=question,
        origin="cycle",
        created_at=_NOW - timedelta(days=age_days),
        last_progress_at=_NOW - timedelta(days=age_days),
        strength=strength,
        decision_impact=decision_impact,
        uncertainty=uncertainty,
    )


class TestComputeDebt:
    """debt = strength x min(age_days, 30) x impact x uncertainty."""

    @pytest.mark.unit
    def test_debt_formula_basic(self):
        # 0.5 x 10 x 0.5 x 0.5 = 1.25 (the doc outline's worked example)
        registry = InvestigationThreadRegistry("case_test")
        thread = _thread(strength=0.5, age_days=10, decision_impact=0.5, uncertainty=0.5)
        assert registry.compute_debt(thread) == pytest.approx(1.25, rel=1e-3)

    @pytest.mark.unit
    def test_debt_age_capped_at_30_days(self):
        registry = InvestigationThreadRegistry("case_test")
        capped = registry.compute_debt(_thread(age_days=100))
        at_cap = registry.compute_debt(_thread(age_days=MAX_AGE_CAP_DAYS))
        assert capped == pytest.approx(at_cap, rel=1e-3)
        assert capped == pytest.approx(0.5 * 30 * 0.5 * 0.5, rel=1e-3)

    @pytest.mark.unit
    def test_debt_floor_0_01_days(self):
        # age 0 (and even future last_progress_at) floors at 0.01 days
        registry = InvestigationThreadRegistry("case_test")
        assert registry.compute_debt(_thread(age_days=0)) == pytest.approx(
            0.5 * 0.01 * 0.5 * 0.5, rel=0.05
        )
        future = _thread(age_days=-5)  # last progress 5 days in the future
        assert registry.compute_debt(future) == pytest.approx(
            0.5 * 0.01 * 0.5 * 0.5, rel=0.05
        )

    @pytest.mark.unit
    def test_debt_zero_strength(self):
        registry = InvestigationThreadRegistry("case_test")
        assert registry.compute_debt(_thread(strength=0.0, age_days=30)) == 0.0

    @pytest.mark.unit
    def test_debt_zero_uncertainty(self):
        registry = InvestigationThreadRegistry("case_test")
        assert registry.compute_debt(_thread(uncertainty=0.0, age_days=30)) == 0.0

    # --- beyond the doc outline ----------------------------------------

    @pytest.mark.unit
    def test_debt_maximal_thread(self):
        # strength 1 x 30 x 1 x 1 = 30 — the ceiling the NBA scorer's
        # min(0.3, debt / 5 x 0.3) cap is calibrated against.
        registry = InvestigationThreadRegistry("case_test")
        thread = _thread(strength=1.0, age_days=100, decision_impact=1.0, uncertainty=1.0)
        assert registry.compute_debt(thread) == pytest.approx(30.0, rel=1e-3)

    @pytest.mark.unit
    def test_debt_monotonic_in_age(self):
        registry = InvestigationThreadRegistry("case_test")
        young = registry.compute_debt(_thread(age_days=1))
        old = registry.compute_debt(_thread(age_days=20))
        assert old > young

    @pytest.mark.unit
    def test_debt_scales_with_impact(self):
        registry = InvestigationThreadRegistry("case_test")
        low = registry.compute_debt(_thread(decision_impact=0.2, age_days=10))
        high = registry.compute_debt(_thread(decision_impact=0.9, age_days=10))
        assert high > low