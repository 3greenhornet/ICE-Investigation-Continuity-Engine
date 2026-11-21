"""Unit tests for case CRUD validation and write shapes (case_engine/case.py).

No real database. ``create_case``'s input validation happens BEFORE the
pool is touched (verified by reading case.py), so invalid-input tests run
with no mocks at all; the happy paths run against an in-memory fake pool
(monkeypatched onto ``case_engine.case.get_pool`` and
``case_engine.get_pool`` for ``_emit_event``) whose acquire() yields a
fake conn that records every execute()/fetchrow()/fetchval() call — this
captures the INSERT parameter shapes (naive-deadline promotion,
required_confidence defaulting per case_type) without Postgres.

Engine surface under test (case_engine/case.py):
    create_case(...)      — title/objective/case_type/confidence/budget
                            validation; naive decision_deadline promoted
                            to UTC (audit gap #40); required_confidence
                            None -> 0.85 debug / 0.0 research|explore
                            (Part 2 §1.4).
    get_case / update_case_status / list_cases — argument validation and
    the idempotent no-op status transition.
"""
from datetime import datetime, timezone

import pytest

import case_engine
from case_engine import case as case_mod
from case_engine.case import create_case, get_case, update_case_status, list_cases
from case_engine.exceptions import CaseNotFoundError


class FakeConn:
    """asyncpg-ish connection over an in-memory cases table."""

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.executions: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args):
        self.executions.append((sql, args))
        sql_u = sql.strip().upper()
        if sql_u.startswith("INSERT INTO CASES"):
            row = {
                "case_id": args[0],
                "title": args[1],
                "objective": args[2],
                "decision_deadline": args[3],
                "required_confidence": args[4],
                "cost_budget_tool_calls": args[5],
                "cost_budget_tokens": args[6],
                "case_type": args[7],
                "status": "open",
                "created_at": args[8],
                "updated_at": args[8],
            }
            self.store[row["case_id"]] = row
        elif sql_u.startswith("UPDATE CASES SET STATUS"):
            row = self.store[args[2]]
            row["status"] = args[0]
            row["updated_at"] = args[1]
        return "OK"

    async def fetchrow(self, sql: str, *args):
        if "FROM CASES" in sql.upper():
            return self.store.get(args[0])
        return None

    async def fetchval(self, sql: str, *_args):
        if "MAX(EVENT_SEQ)" in sql.upper():
            return len(self.executions)
        return 0

    async def fetch(self, _sql: str, *_args):
        return []

    # introspection helpers -------------------------------------------
    @property
    def case_inserts(self):
        return [e for e in self.executions if e[0].strip().upper().startswith("INSERT INTO CASES")]

    @property
    def event_inserts(self):
        return [
            e for e in self.executions
            if e[0].strip().upper().startswith("INSERT INTO CASE_EVENTS")
        ]


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_exc):
        return False


class FakePool:
    def __init__(self):
        self.conn = FakeConn()

    def acquire(self):
        return _FakeAcquire(self.conn)


@pytest.fixture
def fake_pool(monkeypatch):
    pool = FakePool()

    async def _get_pool():
        return pool

    monkeypatch.setattr(case_mod, "get_pool", _get_pool)
    monkeypatch.setattr(case_engine, "get_pool", _get_pool)
    return pool


# ---------------------------------------------------------------------------
# create_case — argument validation (all pre-DB)
# ---------------------------------------------------------------------------

class TestCreateCaseValidation:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_empty_title_raises(self):
        with pytest.raises(ValueError, match="title must be non-empty"):
            await create_case("", "objective")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_empty_objective_raises(self):
        with pytest.raises(ValueError, match="objective must be non-empty"):
            await create_case("title", "   ")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_invalid_confidence_raises(self):
        with pytest.raises(ValueError, match="required_confidence must be in"):
            await create_case("t", "o", required_confidence=1.5)
        with pytest.raises(ValueError, match="required_confidence must be in"):
            await create_case("t", "o", required_confidence=-0.1)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_invalid_case_type_raises(self):
        with pytest.raises(ValueError, match="Invalid case_type"):
            await create_case("t", "o", case_type="incident")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_invalid_budgets_raise(self):
        with pytest.raises(ValueError, match="cost_budget_tool_calls"):
            await create_case("t", "o", cost_budget_tool_calls=0)
        with pytest.raises(ValueError, match="cost_budget_tokens"):
            await create_case("t", "o", cost_budget_tokens=0)


# ---------------------------------------------------------------------------
# create_case — happy path against the fake pool
# ---------------------------------------------------------------------------

class TestCreateCaseWrites:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_valid(self, fake_pool):
        case = await create_case("  Latency regression  ", "Find the root cause")
        assert case.case_id.startswith("case_")
        assert case.title == "Latency regression"      # stripped
        assert case.objective == "Find the root cause"
        assert case.status == "open"
        assert case.case_type == "debug"
        # one INSERT into cases + one case_created event
        assert len(fake_pool.conn.case_inserts) == 1
        assert len(fake_pool.conn.event_inserts) == 1
        # the event payload round-trips: case_created carries case_type
        event_sql, event_args = fake_pool.conn.event_inserts[0]
        assert "case_created" in event_sql or event_args[2] == "case_created"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_naive_deadline_promoted_to_utc(self, fake_pool):
        naive = datetime(2026, 10, 1, 12, 0, 0)  # no tzinfo
        await create_case("t", "o", decision_deadline=naive)
        sql, args = fake_pool.conn.case_inserts[0]
        stored = args[3]  # $4 = decision_deadline
        assert stored.tzinfo is not None
        assert stored.utcoffset().total_seconds() == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_aware_deadline_untouched(self, fake_pool):
        aware = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
        await create_case("t", "o", decision_deadline=aware)
        _sql, args = fake_pool.conn.case_inserts[0]
        assert args[3] is aware

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_required_confidence_debug_defaults_0_85(self, fake_pool):
        await create_case("t", "o", case_type="debug")
        _sql, args = fake_pool.conn.case_inserts[0]
        assert args[4] == 0.85  # $5 = required_confidence

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_required_confidence_research_defaults_0(self, fake_pool):
        await create_case("t", "o", case_type="research")
        _sql, args = fake_pool.conn.case_inserts[0]
        assert args[4] == 0.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_required_confidence_explore_defaults_0(self, fake_pool):
        await create_case("t", "o", case_type="explore")
        _sql, args = fake_pool.conn.case_inserts[0]
        assert args[4] == 0.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_explicit_confidence_wins(self, fake_pool):
        await create_case("t", "o", required_confidence=0.5)
        _sql, args = fake_pool.conn.case_inserts[0]
        assert args[4] == 0.5

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_case_case_type_persisted(self, fake_pool):
        case = await create_case("t", "o", case_type="research")
        assert case.case_type == "research"
        _sql, args = fake_pool.conn.case_inserts[0]
        assert args[7] == "research"  # $8 = case_type


# ---------------------------------------------------------------------------
# get_case / update_case_status / list_cases
# ---------------------------------------------------------------------------

class TestCaseReadsAndStatus:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_case_empty_id_raises(self):
        with pytest.raises(ValueError, match="case_id must be non-empty"):
            await get_case("")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_case_missing_raises_not_found(self, fake_pool):
        with pytest.raises(CaseNotFoundError):
            await get_case("case_missing")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_case_status_invalid_raises_pre_db(self):
        with pytest.raises(ValueError, match="Invalid status"):
            await update_case_status("case_x", "bogus")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_case_status_valid_transition(self, fake_pool):
        case = await create_case("t", "o")
        events_before = len(fake_pool.conn.event_inserts)
        updated = await update_case_status(case.case_id, "investigating", "why")
        assert updated.status == "investigating"
        # exactly one new event: case_status_changed
        assert len(fake_pool.conn.event_inserts) == events_before + 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_case_status_idempotent_no_event(self, fake_pool):
        case = await create_case("t", "o")  # status 'open'
        events_before = len(fake_pool.conn.event_inserts)
        same = await update_case_status(case.case_id, "open", "no-op")
        assert same.status == "open"
        assert len(fake_pool.conn.event_inserts) == events_before  # no new event

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_cases_invalid_limit_raises(self):
        with pytest.raises(ValueError, match="limit must be in"):
            await list_cases(limit=0)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_cases_invalid_status_raises(self):
        with pytest.raises(ValueError, match="Invalid status"):
            await list_cases(status="bogus")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_cases_invalid_case_type_raises(self):
        with pytest.raises(ValueError, match="Invalid case_type"):
            await list_cases(case_type="incident")