"""Postgres integration tests for goal deltas, adjacent proposals, and LAB_PROPOSE actions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from case_engine.action import research_to_lab_proposal
from case_engine.case import create_case
from case_engine.goals import create_goal, list_goals, propose_adjacent_goals, sync_maturity_from_code


def _seed_repointel_sqlite(db_path: Path, *, with_test: bool = False, critical_count: int = 1) -> None:
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE repositories (id TEXT PRIMARY KEY, url TEXT, name TEXT, branch TEXT,
            last_indexed_commit TEXT, last_indexed_at TEXT, status TEXT, config TEXT,
            created_at TEXT, updated_at TEXT, stats TEXT);
        CREATE TABLE files (id TEXT, repo_id TEXT, path TEXT, language TEXT, category TEXT,
            size_bytes INT, content_hash TEXT, created_at TEXT);
        CREATE TABLE nodes (id TEXT PRIMARY KEY, qualified_name TEXT, entity_type TEXT,
            file_path TEXT, source_code TEXT, structural_hash TEXT);
        CREATE TABLE edges (source_id TEXT, target_id TEXT, edge_type TEXT);
        CREATE TABLE findings (id TEXT, repo_id TEXT, tool TEXT, rule_id TEXT, finding_type TEXT,
            severity TEXT, message TEXT, file_path TEXT, line_start INT, line_end INT, symbol_id TEXT,
            confidence REAL, status TEXT, commit_hash TEXT, metadata TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE quality_predictions (id TEXT, repo_id TEXT, file_path TEXT, symbol_id TEXT,
            prediction_type TEXT, probability REAL, confidence REAL);
        """
    )
    conn.execute("INSERT INTO repositories (id, url, name, last_indexed_at, status) VALUES ('R2','x','shop','2026-11-28','ready')")
    conn.executemany(
        "INSERT INTO nodes VALUES (?,?,?,?,?,'')",
        [
            ("file:R2:rerank.py", "rerank.py", "File", "rerank.py", ""),
            (
                "rerank.py::rerank_scores::5",
                "rerank_scores",
                "Function",
                "rerank.py",
                'def rerank_scores(q, docs):\n    """Improve retrieval precision."""\n    return docs\n',
            ),
            ("app.py::handle_query::1", "handle_query", "Function", "app.py", "def handle_query():\n    return rerank_scores('q', [])\n"),
        ],
    )
    conn.executemany(
        "INSERT INTO edges VALUES (?,?,?)",
        [
            ("file:R2:rerank.py", "rerank.py::rerank_scores::5", "CONTAINS"),
            ("file:R2:rerank.py", "app.py::handle_query::1", "CONTAINS"),
            ("app.py::handle_query::1", "rerank.py::rerank_scores::5", "CALLS"),
        ],
    )
    if with_test:
        conn.execute(
            "INSERT INTO nodes VALUES (?,?,?,?,?,'')",
            ("tests/test_rerank.py::test_precision::1", "test_precision", "Function", "tests/test_rerank.py", "def test_precision(): assert rerank_scores('q', [1]) == [1]"),
        )
        conn.execute("INSERT INTO edges VALUES (?,?,?)", ("file:R2:rerank.py", "tests/test_rerank.py::test_precision::1", "CONTAINS"))
    for i in range(critical_count):
        conn.execute(
            "INSERT INTO findings (id, repo_id, tool, rule_id, finding_type, severity, message, file_path, line_start, line_end, confidence, status) "
            "VALUES (?, 'R2', 'audit', 'CRUD-1', 'incomplete_feature', 'critical', 'Missing delete handler', 'rerank.py', 1, 1, 0.9, 'open')",
            (f"f_{i}",),
        )
    conn.commit()
    conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_deltas_adjacent_goals_and_lab_propose_db_roundtrip(db_pool, tmp_path, monkeypatch):
    from case_engine import llm as llm_mod

    monkeypatch.setenv("LLM_FALLBACK_CHAIN", "")
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(llm_mod, "llm_available", lambda *a, **k: False)

    db_file = tmp_path / "repointel.db"
    _seed_repointel_sqlite(db_file, with_test=False, critical_count=1)
    monkeypatch.setenv("REPOINTEL_DB", str(db_file))

    case = await create_case("Loop integration case", "close goal-research-lab loop", case_type="research")
    g = await create_goal(
        case.case_id,
        "Improve retrieval precision",
        clarity_level=3,
        priority=1,
        success_criteria="Rerank scores cleanly; zero critical findings and unit tested",
    )

    first = await sync_maturity_from_code(g["goal_id"], repo="shop")
    assert first["progress_status"] == "baseline"
    assert first["maturity"] == "connected"

    # Improve codebase: clear critical finding + add test
    _seed_repointel_sqlite(db_file, with_test=True, critical_count=0)
    second = await sync_maturity_from_code(g["goal_id"], repo="shop")
    assert second["progress_status"] == "improved"
    assert second["maturity"] == "tested"
    assert second["deltas"]["blocker_critical_findings_delta"] == -1
    assert second["deltas"]["health_score_delta"] > 0

    # Propose adjacent follow-on goals from fresh RepoIntel critical finding
    _seed_repointel_sqlite(db_file, with_test=True, critical_count=1)
    adj = await propose_adjacent_goals(case.case_id, repo="shop", max_proposals=3)
    assert len(adj["proposals"]) >= 1
    all_goals = await list_goals(case.case_id, include_superseded=False)
    assert any(x["origin"] == "ice_proposal" for x in all_goals)

    # Draft REVERSIBLE LAB_PROPOSE action directly in Postgres (validates migration 023)
    act = await research_to_lab_proposal(case.case_id, repo="shop")
    assert act["action_type"] == "LAB_PROPOSE"
    assert act["approval_class"] == "REVERSIBLE"
    assert act["status"] == "proposed"
