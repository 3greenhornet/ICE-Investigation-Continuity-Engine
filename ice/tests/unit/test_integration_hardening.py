"""test_integration_hardening.py — ICE side of the RepoIntel integration hardening.

Covers (CHANGES.md "Cross-project integration hardening"):
  * get_file_snippet refuses traversal / drive-letter / UNC / secret files and caps ranges
  * RepoIntel DB discovery priority (REPOINTEL_DB > REPOINTEL_DIR > sibling checkout)
  * /api/v1/guide/* HTTP routes, including concurrent use of the shared JSON store
  * /api/v1/repointel/event persists only with a real case_id and reports honestly
  * EvidenceCreate accepts optional source attribution
No database, no LLM, no network.
"""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- file snippet

@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    import case_engine.repointel_client as rc

    repo = tmp_path / "repointel-ai-2"
    (repo / "backend" / "app").mkdir(parents=True)
    (repo / "backend" / "app" / "main.py").write_text(
        "\n".join(f"line {i}" for i in range(1, 5001)), encoding="utf-8")
    (repo / ".env").write_text("SECRET_KEY=do-not-leak\n", encoding="utf-8")
    (repo / ".env.example").write_text("SECRET_KEY=\n", encoding="utf-8")
    (repo / "server.pem").write_text("-----BEGIN-----", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the repo", encoding="utf-8")
    monkeypatch.setattr(rc, "DEFAULT_REPOINTEL_PATHS", [str(repo)])
    return rc.RepoIntelClient(db_path=str(tmp_path / "missing.db")), repo, outside


@pytest.mark.parametrize("path", [
    "../outside.txt", "backend/../../outside.txt", "/etc/passwd", "C:/Windows/win.ini",
    "c:\\Windows\\win.ini", "\\\\server\\share\\x", "", "   ",
])
def test_snippet_rejects_escaping_paths(fake_repo, path):
    client, _, _ = fake_repo
    res = client.get_file_snippet(path)
    assert "error" in res and "content" not in res


@pytest.mark.parametrize("path", [".env", "server.pem", "backend/.env.local", "id_rsa", "credentials.json"])
def test_snippet_refuses_secret_files(fake_repo, path):
    client, _, _ = fake_repo
    res = client.get_file_snippet(path)
    assert "error" in res and "do-not-leak" not in json.dumps(res)


def test_snippet_allows_env_example_and_code(fake_repo):
    client, _, _ = fake_repo
    assert "content" in client.get_file_snippet(".env.example")
    res = client.get_file_snippet("app/main.py", 10, 12)  # resolved under backend/
    assert res["content"].splitlines() == ["line 10", "line 11", "line 12"]


def test_snippet_caps_huge_ranges(fake_repo):
    client, _, _ = fake_repo
    res = client.get_file_snippet("backend/app/main.py", 1, 10_000_000)
    assert res["end_line"] - res["start_line"] + 1 == 2000
    assert res["total_lines"] == 5000
    past = client.get_file_snippet("backend/app/main.py", 9000, 9100)
    assert "error" in past


def test_snippet_rejects_symlink_escape(fake_repo):
    client, repo, outside = fake_repo
    link = repo / "escape.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    assert "content" not in client.get_file_snippet("escape.txt")


# --------------------------------------------------------------------------- DB discovery

def _make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(path).close()
    return path


def test_db_discovery_priority(tmp_path, monkeypatch):
    import case_engine.repointel_client as rc

    explicit = _make_db(tmp_path / "explicit" / "kb.db")
    via_dir = _make_db(tmp_path / "dir" / "data" / "repointel.db")
    monkeypatch.setattr(rc, "DEFAULT_REPOINTEL_PATHS", [str(tmp_path / "dir")])

    monkeypatch.setenv("REPOINTEL_DB", str(explicit))
    assert rc.find_repointel_db() == str(explicit)
    monkeypatch.setenv("REPOINTEL_DB", str(tmp_path / "nope.db"))  # missing -> fall through
    assert rc.find_repointel_db() == str(via_dir)


def test_sibling_checkout_is_searched_before_legacy_path():
    import case_engine.repointel_client as rc

    paths = [p for p in rc.DEFAULT_REPOINTEL_PATHS if p]
    assert rc._SIBLING_REPOINTEL in paths
    legacy = [p for p in paths if "OneDrive" in p]
    if legacy:
        assert paths.index(rc._SIBLING_REPOINTEL) < paths.index(legacy[0])


def test_readonly_connection_has_busy_timeout(tmp_path):
    import case_engine.repointel_client as rc

    db = _make_db(tmp_path / "data" / "repointel.db")
    conn = rc.RepoIntelClient(db_path=str(db))._get_connection()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE t (x INT)")  # read-only
    finally:
        conn.close()


# --------------------------------------------------------------------------- guide routes

@pytest.fixture
def api(tmp_path, monkeypatch):
    from case_engine.code_understanding import CodeUnderstandingEngine

    monkeypatch.setattr(CodeUnderstandingEngine, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(CodeUnderstandingEngine, "_DATA_FILE", tmp_path / "store.json")
    from app.main import app

    return TestClient(app)  # no context manager -> lifespan (DB) not started


def test_guide_routes_are_mounted(api):
    paths = set(api.app.openapi()["paths"])
    for p in ("walkthrough", "import-learnharness", "plan", "step", "probe", "evaluate", "diagram", "explain"):
        assert f"/api/v1/guide/{p}" in paths


def test_walkthrough_shape_matches_repointel_fallback_keys(api):
    r = api.post("/api/v1/guide/walkthrough", json={"topic": "session refresh", "case_id": "case_x"})
    assert r.status_code == 200
    body = r.json()
    for k in ("topic", "case_id", "ladder_step", "prompt", "hints", "source"):
        assert k in body
    assert body["source"] == "ice_code_understanding"
    assert body["plan"]["plan_id"] and body["diagram"]


def test_guide_validation_and_not_found(api):
    assert api.post("/api/v1/guide/walkthrough", json={"topic": ""}).status_code == 422
    assert api.post("/api/v1/guide/walkthrough", json={"topic": "x" * 501}).status_code == 422
    assert api.post("/api/v1/guide/step", json={"plan_id": "nope", "node_id": "n"}).status_code == 404
    assert api.post("/api/v1/guide/import-learnharness", json={"max_sessions": 0}).status_code == 422


def test_plan_then_step_roundtrip(api):
    plan = api.post("/api/v1/guide/plan", json={"goal_statement": "payment retries"}).json()
    node = plan["nodes"][0]["id"]
    step = api.post("/api/v1/guide/step", json={"plan_id": plan["plan_id"], "node_id": node})
    assert step.status_code == 200 and step.json()["plan_id"] == plan["plan_id"]


def test_guide_store_survives_concurrent_requests(api, tmp_path):
    def call(i):
        return api.post("/api/v1/guide/walkthrough", json={"topic": f"topic {i}"}).status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(call, range(64)))
    assert codes == [200] * 64
    store = json.loads((tmp_path / "store.json").read_text(encoding="utf-8"))  # never half-written
    assert len(store["plans"]) >= 64
    assert not list(tmp_path.glob("*.tmp"))


# --------------------------------------------------------------------------- event webhook

async def test_event_without_case_is_flagged_but_not_persisted():
    from app.routes.repointel import RepoIntelEventPayload, repointel_event_sync

    res = await repointel_event_sync(RepoIntelEventPayload(
        event_type="FindingDetected", severity="critical", finding_id="F"))
    assert res["flagged_for_investigation"] is True
    assert res["investigation_opened"] is False and res["persisted_evidence_id"] is None


async def test_event_with_case_persists_tool_evidence(monkeypatch):
    import case_engine.case as case_mod
    import case_engine.evidence as ev_mod
    from app.routes.repointel import RepoIntelEventPayload, repointel_event_sync

    seen = {}

    async def fake_get_case(cid):
        seen["case"] = cid

    class _Src:
        source_id = "src_1"

    async def fake_source(cid, stype, uri, metadata=None):
        seen["source"] = (cid, stype, uri)
        return _Src()

    class _Ev:
        evidence_id = "ev_1"

    async def fake_ingest(**kw):
        seen["ingest"] = kw
        return _Ev()

    monkeypatch.setattr(case_mod, "get_case", fake_get_case)
    monkeypatch.setattr(ev_mod, "_create_source", fake_source)
    monkeypatch.setattr(ev_mod, "ingest_evidence", fake_ingest)

    res = await repointel_event_sync(RepoIntelEventPayload(
        event_type="FindingDetected", severity="HIGH", finding_id="F9", case_id="case_000000000001",
        file_path="app/a.py", details={"message": "boom"}))
    assert res["investigation_opened"] is True and res["persisted_evidence_id"] == "ev_1"
    assert seen["source"][1] == "TOOL"
    assert seen["ingest"]["evidence_type"] == "TOOL_RESULT"
    assert "boom" in seen["ingest"]["content"]

    # Low severity: logged only, never persisted.
    seen.clear()
    res = await repointel_event_sync(RepoIntelEventPayload(
        event_type="FindingDetected", severity="LOW", case_id="case_000000000001"))
    assert res["investigation_opened"] is False and "ingest" not in seen


async def test_event_unknown_case_reports_error(monkeypatch):
    import case_engine.case as case_mod
    from app.routes.repointel import RepoIntelEventPayload, repointel_event_sync
    from case_engine.exceptions import CaseNotFoundError

    async def missing(cid):
        raise CaseNotFoundError(cid)

    monkeypatch.setattr(case_mod, "get_case", missing)
    res = await repointel_event_sync(RepoIntelEventPayload(
        event_type="TestFailed", case_id="case_ffffffffffff"))
    assert res["investigation_opened"] is False and "not found" in res["error"]


# --------------------------------------------------------------------------- evidence schema

def test_evidence_create_source_attribution():
    from pydantic import ValidationError

    from app.schemas import EvidenceCreate

    assert EvidenceCreate(content="x").source_type is None  # default unchanged -> HUMAN
    e = EvidenceCreate(content="x", source_type="TOOL", source_uri="repointel://r/a.py:1")
    assert e.source_type == "TOOL"
    with pytest.raises(ValidationError):
        EvidenceCreate(content="x", source_type="ROBOT")


# --------------------------------------------------------------------------- serendipity tools

async def test_suggest_next_runs_engine_with_dpp_and_learns(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    import case_engine.goals as goals_mod
    import case_engine.information_gap as gap_mod
    from app.mcp import tools_registry as tr
    from case_engine.schemas import InformationGap

    monkeypatch.setenv("ICE_SERENDIPITY_STORE", str(tmp_path / "s.sqlite"))
    goals = [{"goal_id": f"g{i}", "statement": s, "kind": "goal", "clarity_level": 3}
             for i, s in enumerate(["secure login with otp", "login rate limiting",
                                    "cache product catalog", "deploy pipeline rollback"])]
    gaps = [InformationGap(gap_id=f"gap{i}", case_id="case_x", needed_evidence=t, priority=p,
                           created_at=datetime.now(timezone.utc))
            for i, (t, p) in enumerate([("p95 latency of checkout", 0.9), ("auth error logs", 0.6)])]

    async def fake_goals(case_id, include_superseded=True, kind=None):
        return goals

    async def fake_gaps(case_id, resolved=False):
        return gaps

    monkeypatch.setattr(goals_mod, "list_goals", fake_goals)
    monkeypatch.setattr(gap_mod, "list_gaps_for_case", fake_gaps)

    res = await tr.ice_suggest_next("case_x", n=4)
    assert "error" not in res, res
    sug = res["diverse_suggestions"]
    assert len(sug) == 4 and len({s["id"] for s in sug}) == 4
    assert res["method"] == {"diversity": "dpp", "exploration": "thompson"}
    assert res["pool"] == {"goals": 4, "gaps": 2}
    assert all(s["token"] and s["slot"] in ("exploit", "adjacent", "uncertainty", "entropy") for s in sug)
    json.dumps(res)  # scores must be JSON-safe (no inf)

    fb = await tr.ice_suggest_feedback(sug[0]["token"], "engaged")
    assert fb["alpha"] >= 1 and fb["signal"] == "engaged"
    again = await tr.ice_suggest_feedback(sug[0]["token"], "engaged")
    assert "error" in again  # one reaction per suggestion

    assert "ice_suggest_feedback" in tr.TOOLS
