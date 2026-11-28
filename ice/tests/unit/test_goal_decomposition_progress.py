"""Unit tests for abstract-goal decomposition and measurable progress deltas."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from case_engine import goals as goals_mod
from case_engine.repointel_client import RepoIntelClient


def _build_mock_repointel_db(db_path: Path, *, with_test: bool = False, blocker_count: int = 1) -> str:
    conn = sqlite3.connect(str(db_path))
    repo_id = "repo_demo"
    file_prefix = f"file:{repo_id}:"
    conn.executescript(
        """
        CREATE TABLE repositories (id TEXT PRIMARY KEY, name TEXT, url TEXT, branch TEXT, last_indexed_at TEXT, stats TEXT);
        CREATE TABLE files (id TEXT PRIMARY KEY, repo_id TEXT, path TEXT, language TEXT, size_bytes INT, content_hash TEXT);
        CREATE TABLE nodes (id TEXT PRIMARY KEY, qualified_name TEXT, entity_type TEXT, file_path TEXT, source_code TEXT);
        CREATE TABLE edges (source_id TEXT, target_id TEXT, edge_type TEXT);
        CREATE TABLE findings (id TEXT PRIMARY KEY, repo_id TEXT, rule_id TEXT, severity TEXT, finding_type TEXT, message TEXT, file_path TEXT, line_start INT);
        """
    )
    conn.execute(
        "INSERT INTO repositories VALUES (?, 'demo', 'http://local/demo', 'main', '2026-11-28T00:00:00Z', '{}')",
        (repo_id,),
    )
    # File node (for repo scoping via CONTAINS edge)
    conn.execute(
        "INSERT INTO nodes VALUES (?, 'retrieval.py', 'File', 'retrieval.py', '')",
        (f"{file_prefix}retrieval.py",),
    )
    # Abstract-concept function where 'precision' is in docstring, NOT symbol name
    conn.execute(
        "INSERT INTO nodes VALUES (?, 'rerank_candidates', 'Function', 'retrieval.py', ?)",
        (
            "fn_rerank",
            'def rerank_candidates(query, docs):\n    """Improve retrieval precision with cross-encoder scoring."""\n    return sorted(docs, key=lambda d: d["score"], reverse=True)\n',
        ),
    )
    conn.execute(
        "INSERT INTO nodes VALUES (?, 'placeholder_export', 'Function', 'export.py', ?)",
        ("fn_stub", 'def placeholder_export():\n    """TODO stub."""\n    pass\n'),
    )
    conn.execute("INSERT INTO edges VALUES (?, 'fn_rerank', 'CONTAINS')", (f"{file_prefix}retrieval.py",))
    conn.execute("INSERT INTO edges VALUES (?, 'fn_stub', 'CONTAINS')", (f"{file_prefix}retrieval.py",))

    if with_test:
        conn.execute(
            "INSERT INTO nodes VALUES (?, 'test_rerank_precision', 'Function', 'tests/test_retrieval.py', ?)",
            ("fn_test", "def test_rerank_precision():\n    assert rerank_candidates('q', [{'score': 1}])\n"),
        )
        conn.execute("INSERT INTO edges VALUES (?, 'fn_test', 'CONTAINS')", (f"{file_prefix}retrieval.py",))

    for i in range(blocker_count):
        conn.execute(
            "INSERT INTO findings VALUES (?, ?, 'R001', 'critical', 'bug', 'bad issue', 'retrieval.py', ?)",
            (f"find_{i}", repo_id, 10 + i),
        )
    conn.commit()
    conn.close()
    return repo_id


def test_decompose_abstract_goal_and_detect_scaffolded_vs_tested(tmp_path: Path):
    db_file = tmp_path / "repointel.db"
    _build_mock_repointel_db(db_file, with_test=False, blocker_count=1)
    client = RepoIntelClient(db_path=str(db_file), repo="demo")

    # 1. Pure stub detection returns 'scaffolded'
    stub_assess = client.check_goal_code_maturity("g_stub", "Build placeholder export feature")
    assert stub_assess.suggested_maturity == "scaffolded"
    assert stub_assess.checks_passed == 0

    # 2. Abstract goal with concrete success criteria decomposes and finds docstring/implementation match
    a1 = client.check_goal_code_maturity(
        "g_prec",
        "Improve retrieval precision",
        success_criteria="Rerank candidates correctly; zero critical findings and tested in unit tests",
    )
    assert a1.checks_total >= 2
    assert len(a1.candidate_symbols) >= 1
    assert any("rerank_candidates" in s for s in a1.candidate_symbols)
    assert a1.metrics["blocker_critical_findings"] == 1

    # 3. After adding tests and clearing findings, quantitative metrics & deltas reflect real progress
    _build_mock_repointel_db(tmp_path / "repointel2.db", with_test=True, blocker_count=0)
    client2 = RepoIntelClient(db_path=str(tmp_path / "repointel2.db"), repo="demo")
    a2 = client2.check_goal_code_maturity(
        "g_prec",
        "Improve retrieval precision",
        success_criteria="Rerank candidates correctly; zero critical findings and tested in unit tests",
    )
    assert a2.suggested_maturity == "tested"
    assert a2.checks_passed >= a1.checks_passed
    deltas, status = goals_mod._compute_progress_deltas(
        a1.suggested_maturity, a2.suggested_maturity, a1.metrics, a2.metrics
    )
    assert status == "improved"
    assert deltas["maturity_step_delta"] > 0
    assert deltas["blocker_critical_findings_delta"] == -1
    assert deltas["health_score_delta"] > 0
