"""Unit tests for check_temporal_compatibility.

No database, no LLM. Fast. Verifies the pre-LLM screening logic that
prevents false-positive contradictions when claims apply to different
time windows.

This file is the doc §5.3 complete example 1's ``TestTemporalCompatibility``
class, split from the scope class into its own file to match the §5.1
11-file unit tree (every test from the doc example is kept).

Engine surface under test (case_engine/contradiction.py):
    check_temporal_compatibility(claim_a, claim_b) -> (bool, reason | None)
    Rules: either claim fully unbounded -> compatible; missing bounds are
    -inf/+inf; disjoint ranges -> incompatible ("Non-overlapping ...").
"""
from datetime import datetime, timezone

import pytest

from case_engine.schemas import Claim
from case_engine.contradiction import check_temporal_compatibility


def _claim(
    statement: str,
    scope: dict = None,
    valid_from: datetime = None,
    valid_until: datetime = None,
) -> Claim:
    """Helper: build a Claim object for testing."""
    now = datetime.now(timezone.utc)
    return Claim(
        claim_id="c_test",
        case_id="case_test",
        statement=statement,
        claim_type="MEASUREMENT",
        scope=scope,
        valid_from=valid_from,
        valid_until=valid_until,
        confidence=0.8,
        created_at=now,
        updated_at=now,
    )


class TestTemporalCompatibility:
    """Tests for check_temporal_compatibility."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_both_unbounded_compatible(self):
        a = _claim("latency rose")
        b = _claim("latency fell")
        compatible, reason = await check_temporal_compatibility(a, b)
        assert compatible is True
        assert reason is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_one_unbounded_compatible(self):
        a = _claim("latency rose")
        b = _claim(
            "latency fell", valid_from=datetime(2026, 9, 14, tzinfo=timezone.utc)
        )
        compatible, reason = await check_temporal_compatibility(a, b)
        assert compatible is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_overlapping_ranges_compatible(self):
        a = _claim(
            "latency rose",
            valid_from=datetime(2026, 9, 10, tzinfo=timezone.utc),
            valid_until=datetime(2026, 9, 15, tzinfo=timezone.utc),
        )
        b = _claim(
            "latency fell",
            valid_from=datetime(2026, 9, 13, tzinfo=timezone.utc),
            valid_until=datetime(2026, 9, 20, tzinfo=timezone.utc),
        )
        compatible, reason = await check_temporal_compatibility(a, b)
        assert compatible is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_touching_boundary_compatible(self):
        a = _claim(
            "latency rose",
            valid_from=datetime(2026, 9, 10, tzinfo=timezone.utc),
            valid_until=datetime(2026, 9, 13, tzinfo=timezone.utc),
        )
        b = _claim(
            "latency fell",
            valid_from=datetime(2026, 9, 13, tzinfo=timezone.utc),
            valid_until=datetime(2026, 9, 20, tzinfo=timezone.utc),
        )
        compatible, reason = await check_temporal_compatibility(a, b)
        assert compatible is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_disjoint_ranges_incompatible(self):
        a = _claim(
            "latency rose",
            valid_from=datetime(2026, 9, 10, tzinfo=timezone.utc),
            valid_until=datetime(2026, 9, 13, tzinfo=timezone.utc),
        )
        b = _claim(
            "latency fell",
            valid_from=datetime(2026, 9, 15, tzinfo=timezone.utc),
            valid_until=datetime(2026, 9, 20, tzinfo=timezone.utc),
        )
        compatible, reason = await check_temporal_compatibility(a, b)
        assert compatible is False
        assert "Non-overlapping" in reason

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_only_start_bound_before_other_start_compatible(self):
        # a = [9/10, +inf), b = [9/20, +inf) — overlap
        a = _claim(
            "latency rose", valid_from=datetime(2026, 9, 10, tzinfo=timezone.utc)
        )
        b = _claim(
            "latency fell", valid_from=datetime(2026, 9, 20, tzinfo=timezone.utc)
        )
        compatible, reason = await check_temporal_compatibility(a, b)
        assert compatible is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_only_end_bound_before_other_end_compatible(self):
        # a = (-inf, 9/10], b = (-inf, 9/20] — overlap
        a = _claim(
            "latency rose", valid_until=datetime(2026, 9, 10, tzinfo=timezone.utc)
        )
        b = _claim(
            "latency fell", valid_until=datetime(2026, 9, 20, tzinfo=timezone.utc)
        )
        compatible, reason = await check_temporal_compatibility(a, b)
        assert compatible is True

    # --- beyond the doc outline: mirrored disjoint + reason shape -----

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_disjoint_reversed_order_incompatible(self):
        # b entirely before a — the mirrored branch of the disjoint check
        a = _claim(
            "latency rose",
            valid_from=datetime(2026, 9, 15, tzinfo=timezone.utc),
            valid_until=datetime(2026, 9, 20, tzinfo=timezone.utc),
        )
        b = _claim(
            "latency fell",
            valid_from=datetime(2026, 9, 10, tzinfo=timezone.utc),
            valid_until=datetime(2026, 9, 13, tzinfo=timezone.utc),
        )
        compatible, reason = await check_temporal_compatibility(a, b)
        assert compatible is False
        assert "Non-overlapping" in reason
        assert "2026-09-10" in reason

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_both_bounds_on_one_side_only_compatible(self):
        # a bounded, b has both bounds later but overlapping by one day
        a = _claim(
            "latency rose",
            valid_from=datetime(2026, 9, 10, tzinfo=timezone.utc),
            valid_until=datetime(2026, 9, 12, tzinfo=timezone.utc),
        )
        b = _claim(
            "latency fell",
            valid_from=datetime(2026, 9, 12, tzinfo=timezone.utc),
            valid_until=datetime(2026, 9, 13, tzinfo=timezone.utc),
        )
        compatible, _reason = await check_temporal_compatibility(a, b)
        assert compatible is True