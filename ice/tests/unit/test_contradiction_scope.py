"""Unit tests for check_scope_compatibility.

No database, no LLM. Fast. Verifies the pre-LLM screening logic that
prevents false-positive contradictions when claims apply to different
contexts.

This file is the doc §5.3 complete example 1's ``TestScopeCompatibility``
class, kept verbatim except: (a) it is split from the temporal class into
its own file to match the §5.1 11-file unit tree, and (b) the Claim
constructor matches case_engine.schemas.Claim exactly (status and the
evidence-id lists default; every other field the doc passes exists
verbatim on our model).

Engine surface under test (case_engine/contradiction.py):
    check_scope_compatibility(claim_a, claim_b) -> (bool, reason | None)
    Rules: both/one empty -> compatible; shared keys must agree (scalars
    equal, lists must share >= 1 element); first disqualifying key wins.
"""
from datetime import datetime, timezone

import pytest

from case_engine.schemas import Claim
from case_engine.contradiction import check_scope_compatibility


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


class TestScopeCompatibility:
    """Tests for check_scope_compatibility."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_both_scopes_none_is_compatible(self):
        a = _claim("latency rose")
        b = _claim("latency fell")
        compatible, reason = await check_scope_compatibility(a, b)
        assert compatible is True
        assert reason is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_both_scopes_empty_is_compatible(self):
        a = _claim("latency rose", scope={})
        b = _claim("latency fell", scope={})
        compatible, reason = await check_scope_compatibility(a, b)
        assert compatible is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_one_scope_none_is_compatible(self):
        a = _claim("latency rose", scope={"region": "us-east"})
        b = _claim("latency fell")
        compatible, reason = await check_scope_compatibility(a, b)
        assert compatible is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_same_scalar_value_compatible(self):
        a = _claim("latency rose", scope={"region": "us-east"})
        b = _claim("latency fell", scope={"region": "us-east"})
        compatible, reason = await check_scope_compatibility(a, b)
        assert compatible is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_different_scalar_value_incompatible(self):
        a = _claim("latency rose", scope={"region": "us-east"})
        b = _claim("latency fell", scope={"region": "eu-west"})
        compatible, reason = await check_scope_compatibility(a, b)
        assert compatible is False
        assert "Different region" in reason
        assert "us-east" in reason
        assert "eu-west" in reason

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_overlapping_lists_compatible(self):
        a = _claim("latency rose", scope={"region": ["us-east", "us-west"]})
        b = _claim("latency fell", scope={"region": ["us-east", "eu-north"]})
        compatible, reason = await check_scope_compatibility(a, b)
        assert compatible is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_disjoint_lists_incompatible(self):
        a = _claim("latency rose", scope={"region": ["us-east", "us-west"]})
        b = _claim("latency fell", scope={"region": ["eu-west", "eu-north"]})
        compatible, reason = await check_scope_compatibility(a, b)
        assert compatible is False
        assert "Disjoint region" in reason

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_multiple_keys_all_must_be_compatible(self):
        # Same region but different service — incompatible
        a = _claim("latency rose", scope={"region": "us-east", "service": "checkout"})
        b = _claim("latency fell", scope={"region": "us-east", "service": "catalog"})
        compatible, reason = await check_scope_compatibility(a, b)
        assert compatible is False
        assert "Different service" in reason

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_multiple_keys_overlapping_compatible(self):
        a = _claim("latency rose", scope={"region": "us-east", "service": "checkout"})
        b = _claim(
            "latency fell",
            scope={"region": "us-east", "service": "checkout", "env": "prod"},
        )
        compatible, reason = await check_scope_compatibility(a, b)
        assert compatible is True

    # --- beyond the doc outline: scalar-vs-list mix -------------------

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scalar_overlapping_with_list_compatible(self):
        # Rule 2c: either value a list -> any shared element -> compatible
        a = _claim("latency rose", scope={"region": "us-east"})
        b = _claim("latency fell", scope={"region": ["us-east", "eu-north"]})
        compatible, _reason = await check_scope_compatibility(a, b)
        assert compatible is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scalar_disjoint_with_list_incompatible(self):
        a = _claim("latency rose", scope={"region": "us-east"})
        b = _claim("latency fell", scope={"region": ["eu-west", "eu-north"]})
        compatible, reason = await check_scope_compatibility(a, b)
        assert compatible is False
        assert "Disjoint region" in reason