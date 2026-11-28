"""conftest.py — Shared pytest fixtures (spec Part 4 §18.1).

Fixtures
--------
postgres_container
    Session-scoped testcontainers Postgres 16 with pgvector
    (``pgvector/pgvector:pg16``). Skips (rather than errors) when
    testcontainers is not installed.

db_pool
    Function-scoped asyncpg pool (min 1 / max 5, ``init=register_vector``)
    bound to ``db.connection._pool`` so that case_engine modules transparently
    use it. Applies migrations 012a/012/013/014 plus the Part 2 additions
    015/016/017 (read from ``db/migrations/``, in that order — 012a is the
    legacy-data safety check that must precede 012's table drops) once per
    session on a fresh database. When ``DATABASE_URL`` is set (e.g. the CI
    integration job's service container), that database is used and no
    container is started.

case_id
    A freshly created case via ``case_engine.case.create_case``.

Deviation from the spec text (documented):
    * The spec re-applied the migration files on every ``db_pool`` use, but the
      DDL is not idempotent (plain ``CREATE TABLE``), so migrations are applied
      once per session and skipped when the schema already exists.
    * ``case_id`` is an async fixture (pytest-asyncio, ``asyncio_mode = auto``)
      instead of the spec's deprecated ``asyncio.get_event_loop().run_until_complete``.
"""

from pathlib import Path
import asyncio
import concurrent.futures
import os
import re
import uuid as _uuid

import pytest
try:
    import asyncpg
    from pgvector.asyncpg import register_vector
    _POSTGRES_DEPS_AVAILABLE = True
except ImportError:  # unit-only environments should still collect cleanly
    asyncpg = None  # type: ignore[assignment]
    register_vector = None  # type: ignore[assignment]
    _POSTGRES_DEPS_AVAILABLE = False

if _POSTGRES_DEPS_AVAILABLE:
    import db.connection
else:
    db = None  # type: ignore[assignment]

try:  # optional dev extra — file must still parse without testcontainers installed
    from testcontainers.postgres import PostgresContainer

    _TESTCONTAINERS_AVAILABLE = True
except ImportError:  # pragma: no cover
    PostgresContainer = None  # type: ignore[assignment]
    _TESTCONTAINERS_AVAILABLE = False

_ROOT = Path(__file__).resolve().parent
# Order matters (merge spec Part 1 §5.8 P8): 012a is the safety check that
# must run BEFORE 012 drops the (legacy, empty) hypotheses/contradictions
# tables. Lexicographic order would put "012a..." after "012_..." ('a' >
# '_'), so the list is ordered explicitly rather than sorted.
# Part 2 (fix-2-a) additions: 015 (source_llm/origin/case_type columns),
# 016 (contradiction claim_pair_key + unique constraints — duplicates from
# prior runs would abort the ADD CONSTRAINT; fresh test DBs have none),
# 017 (proposed_edges). All three are idempotent (IF NOT EXISTS guards),
# so re-application on a warm schema is safe.
_ICE_MIGRATIONS = [
    "db/migrations/012a_safety_check.sql",
    "db/migrations/012_case_model.sql",
    "db/migrations/013_evidence_graph.sql",
    "db/migrations/014_case_events.sql",
    "db/migrations/015_external_analysis_and_case_type.sql",
    "db/migrations/016_contradiction_uniqueness.sql",
    "db/migrations/017_proposed_edges.sql",
]
# Hari ICE improvements pass: 018-020 are fully idempotent (DO blocks +
# IF NOT EXISTS everywhere), so they are re-applied on every session even
# against a warm schema — the 012-017 files above are not idempotent and
# are therefore only applied to a fresh database.
_ICE_MIGRATIONS_IDEMPOTENT = [
    "db/migrations/018_reasoning_loop.sql",
    "db/migrations/019_plan_and_workspace.sql",
    "db/migrations/020_llm_ledger_and_mechanism_lab.sql",
    "db/migrations/021_project_goals.sql",
    "db/migrations/022_goal_attributes.sql",
    "db/migrations/023_lab_propose_action.sql",
]
_schema_ready = False


_DSN_RE = re.compile(
    r"^(?P<schema>postgres(?:ql)?(?:\+psycopg2)?://)"
    r"(?P<auth>[^/?]*)"
    r"(?P<db>/[^/?]*)"
    r"(?P<query>\?.*)?$"
)


@pytest.fixture(scope="session")
def _session_db(request):
    """Per-session FRESH database (2026-11-16 hardening pass).

    Previously every session shared the single database named by
    DATABASE_URL and nothing ever cleaned up: each run left its cases,
    evidence and actions behind, and later runs inherited them. Tests that
    legitimately depend on global state (cross-case connection ranking,
    the explore ladder's workspace passes) flipped outcomes once enough
    leftovers accumulated — the suite was green only on a virgin
    database, and every rerun on a warm server drifted further. Each
    session now gets its OWN database (created at session start, dropped
    WITH (FORCE) at session end), restoring first-run semantics for every
    run. When DATABASE_URL is unset, the testcontainers path (already a
    fresh container per session) is unchanged. Falls back to the shared
    database if CREATE DATABASE is not permitted — same behavior as
    before this change, never worse.
    """
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if dsn and not dsn.startswith(("postgresql://", "postgres://")):
        dsn = ""
    if not dsn:
        yield None
        return

    m = _DSN_RE.match(dsn)
    if m is None:  # unexpected DSN shape — do not guess
        yield None
        return

    dbname = f"ice_test_{_uuid.uuid4().hex[:8]}"

    async def _admin(statements):
        conn = await asyncpg.connect(dsn)
        try:
            for stmt in statements:
                await conn.execute(stmt)
        finally:
            await conn.close()

    def _admin_sync(statements):
        # The fixture is resolved lazily from inside db_pool's event loop
        # (async fixture -> getfixturevalue on a session fixture), so
        # asyncio.run() in THIS thread is illegal ("cannot be called from a
        # running event loop"). A dedicated worker thread has no running
        # loop, which makes asyncio.run legal from any resolution context.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(
                lambda: asyncio.run(_admin(statements))
            ).result()

    try:
        _admin_sync([f'CREATE DATABASE "{dbname}"'])
    except Exception as exc:
        # no CREATE privilege / nested transaction quirk — behave exactly
        # like the pre-hardening harness (shared database)
        print(
            f"\n[_session_db] fresh-database fallback ({type(exc).__name__}: "
            f"{str(exc)[:100]}) — session will share DATABASE_URL",
            flush=True,
        )
        yield None
        return

    fresh = (
        f"{m.group('schema')}{m.group('auth')}/{dbname}"
        f"{m.group('query') or ''}"
    )
    try:
        yield fresh
    finally:
        for stmts in (
            [f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'],  # PG13+
            [
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{dbname}'",
                f'DROP DATABASE IF EXISTS "{dbname}"',
            ],
        ):
            try:
                _admin_sync(stmts)
                break
            except Exception:
                continue


@pytest.fixture(scope="session")
def postgres_container():
    """Session-scoped throwaway Postgres 16 + pgvector (testcontainers)."""
    if not _POSTGRES_DEPS_AVAILABLE:
        pytest.skip("asyncpg/pgvector not installed — run: pip install -e '.[dev]'")
    if not _TESTCONTAINERS_AVAILABLE:
        pytest.skip("testcontainers not installed — run: pip install -e '.[dev]'")
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg


@pytest.fixture
async def db_pool(request):
    """asyncpg pool installed as db.connection._pool, ICE schema applied."""
    global _schema_ready
    if not _POSTGRES_DEPS_AVAILABLE:
        pytest.skip("asyncpg/pgvector not installed — run: pip install -e '.[dev]'")

    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if dsn and not dsn.startswith(("postgresql://", "postgres://")):
        # fix-3 harness guard: a DATABASE_URL pointing at a non-Postgres
        # store (e.g. "file:.../custom.db" set for unrelated tooling in
        # developer sandboxes) is not usable by asyncpg — previously this
        # raised ClientConfigurationError as a fixture ERROR instead of a
        # skip. Treat it exactly like an unset DATABASE_URL: fall back to
        # the testcontainers path, which SKIPS cleanly when testcontainers
        # is not installed.
        dsn = ""
    if not dsn:
        # Lazy resolution: only start a container when DATABASE_URL is absent
        # (the CI integration job provides its own pgvector service container).
        container = request.getfixturevalue("postgres_container")
        dsn = container.get_connection_url()
        # testcontainers hands out a SQLAlchemy-style DSN; asyncpg wants plain postgresql://
        dsn = dsn.replace("postgresql+psycopg2://", "postgresql://")
    else:
        # Per-session fresh database (see _session_db): every run starts
        # from a virgin schema instead of inheriting previous runs' rows.
        fresh = request.getfixturevalue("_session_db")
        if fresh:
            dsn = fresh

    # Phase-0 harness fix (Hari ICE improvements doc: "run the actual
    # integration suite"): register_vector (the pool's init callback)
    # introspects the public.vector type at connection time, so the
    # extension MUST exist before the pool's eagerly-established first
    # connection. Bootstrapping it on a plain connection first mirrors
    # db/connection.init_db and fixes the previous
    # "ValueError: unknown type: public.vector" fixture ERROR on any
    # database where the extension was not pre-created (fresh pgserver
    # data dirs, plain postgres:pg16 containers without an init script).
    bootstrap = await asyncpg.connect(dsn)
    try:
        await bootstrap.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    finally:
        await bootstrap.close()

    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=5,
        init=register_vector,
    )
    db.connection._pool = pool

    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        if not _schema_ready:
            already_migrated = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'cases'
                )
                """
            )
            if not already_migrated:
                for migration in _ICE_MIGRATIONS:
                    with open(_ROOT / migration) as fh:
                        await conn.execute(fh.read())
            # Idempotent additions always applied (warm-schema safe).
            for migration in _ICE_MIGRATIONS_IDEMPOTENT:
                with open(_ROOT / migration) as fh:
                    await conn.execute(fh.read())
            _schema_ready = True

    yield pool

    db.connection._pool = None
    await pool.close()


@pytest.fixture
async def case_id(db_pool):
    """A fresh case created through case_engine (emits case_created event)."""
    from case_engine.case import create_case

    case = await create_case("Test", "Test objective")
    return case.case_id