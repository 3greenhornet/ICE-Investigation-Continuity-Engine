"""Goal attributes (migration 022), revision by supersession, and the
RepoIntel -> goal maturity loop (sync_maturity_from_code)."""
import sqlite3
from datetime import date

import pytest

from case_engine.case import create_case
from case_engine.goals import (
    create_goal, get_goal, latest_maturity, list_goals, revise_goal, sync_maturity_from_code,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_goal_attributes_roundtrip_and_validation(db_pool):
    case = await create_case("Attr case", "goals with planning data")
    g = await create_goal(case.case_id, "Answer questions in under 10 seconds", kind="quality_objective",
                          clarity_level=4, priority=1, success_criteria="p95 < 10 s on the demo repo",
                          tags=["Perf", "latency", "perf"], target_date="2026-12-31",
                          metadata={"owner": "Bharath"})
    assert g["priority"] == 1
    assert g["tags"] == ["latency", "perf"]          # normalised, deduplicated
    assert g["target_date"] == date(2026, 12, 31)
    assert g["metadata"] == {"owner": "Bharath"}
    assert g["success_criteria"].startswith("p95")
    with pytest.raises(ValueError):
        await create_goal(case.case_id, "bad", priority=9)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_revise_supersedes_and_keeps_unchanged_fields(db_pool):
    case = await create_case("Revise case", "revision keeps history")
    g = await create_goal(case.case_id, "Ship dashboard", clarity_level=3, priority=3, tags=["ui"])
    g2 = await revise_goal(g["goal_id"], priority=1, tags=["ui", "release"])
    assert g2["version"] == 2 and g2["supersedes"] == g["goal_id"]
    assert g2["statement"] == "Ship dashboard" and g2["clarity_level"] == 3
    assert g2["priority"] == 1 and g2["tags"] == ["release", "ui"]
    old = await get_goal(g["goal_id"])
    assert old["status"] == "superseded" and old["superseded_by"] == g2["goal_id"]
    with pytest.raises(ValueError):
        await revise_goal(g["goal_id"], priority=2)          # already superseded
    with pytest.raises(ValueError):
        await revise_goal(g2["goal_id"], case_id="other")    # not a revisable field
    active = await list_goals(case.case_id, include_superseded=False)
    assert [x["goal_id"] for x in active] == [g2["goal_id"]]


def _repointel_db(path):
    c = sqlite3.connect(path)
    c.executescript("""
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
      CREATE TABLE documents (id TEXT, repo_id TEXT, file_path TEXT, doc_type TEXT, content TEXT);
    """)
    c.execute("INSERT INTO repositories (id, url, name, last_indexed_at, status) VALUES ('R1','x','shop','2026-01-01','ready')")
    c.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,'')", [
        ("file:R1:auth.py", "auth.py", "File", "auth.py", ""),
        ("auth.py::rate_limit_login::3", "rate_limit_login", "Function", "auth.py", "def rate_limit_login(): ..."),
        ("auth.py::login::10", "login", "Function", "auth.py", "def login(): rate_limit_login()"),
    ])
    c.executemany("INSERT INTO edges VALUES (?,?,?)", [
        ("file:R1:auth.py", "auth.py::rate_limit_login::3", "CONTAINS"),
        ("file:R1:auth.py", "auth.py::login::10", "CONTAINS"),
        ("auth.py::login::10", "auth.py::rate_limit_login::3", "CALLS"),
    ])
    c.commit()
    c.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_maturity_from_code_records_evidence_backed_progress(db_pool, tmp_path, monkeypatch):
    db = tmp_path / "repointel.db"
    _repointel_db(db)
    monkeypatch.setenv("REPOINTEL_DB", str(db))
    case = await create_case("Maturity case", "goal to code loop")
    g = await create_goal(case.case_id, "Rate limit login attempts", clarity_level=3)
    res = await sync_maturity_from_code(g["goal_id"], repo="shop")
    assert res["repo_id"] == "R1"
    assert res["evidence_ids"] and res["assessed_by"] == "ice"
    assert res["maturity"] in ("candidate", "connected", "scaffolded", "tested", "no_candidate")
    latest = await latest_maturity(g["goal_id"])
    assert latest["maturity"] == res["maturity"]
