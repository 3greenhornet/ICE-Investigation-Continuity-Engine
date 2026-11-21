#!/usr/bin/env python3
"""fix-4 P2 polarity harness — ICE fixes doc P2.5 three-part test, sandbox form.

The doc (lines 12706-12760) runs this against the live api container:

  Test 1: evidence -> hypothesis SUPPORTS edge  -> confidence rises above 0.5
  Test 2: evidence -> hypothesis CONTRADICTS   -> confidence falls below 0.5
  Test 3: claim    -> hypothesis CONTRADICTS   -> confidence falls below 0.5
          (Correction 6: the old Oct 22 loop treated every claim edge as
           SUPPORTS, so this case silently RAISED confidence)

This sandbox has no Postgres, so the same engine functions run against an
in-memory fake asyncpg pool (pattern: .scratch/ice4a_live_http.py /
fix2b_verify.py). The SQL handlers below reproduce the exact column layouts
of migrations 012/013/015 (cases, sources, evidence, claims, hypotheses,
hypothesis_versions, evidence_graph_edges, case_events), so the REAL
create_case / _create_source / ingest_evidence / create_hypothesis /
create_claim / add_edge / recompute_hypothesis_confidence /
get_current_version code paths execute — only the storage is faked.

The live-DB form of this exact test is the doc's D.4 contract item 7 and
runs user-side (see verification/2026-01-24/03+04 blocked notes).
"""
import asyncio
import json
import sys
import types
from datetime import datetime, timezone

HARI = "/home/z/my-project/hari"
sys.path.insert(0, HARI)

FAILURES = []


def check(name, cond, extra=""):
    ok = bool(cond)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {extra}" if (extra and not ok) else ""))
    if not ok:
        FAILURES.append(name)
    return ok


# --------------------------------------------------------------------------
# stubs (pattern from .scratch/final_import_sweep.py)
# --------------------------------------------------------------------------

def _mk_async(name):
    async def f(*a, **k):
        raise RuntimeError("stub-%s" % name)
    return f


def install_stubs():
    asyncpg = types.ModuleType("asyncpg")
    asyncpg.Pool = type("Pool", (), {})
    asyncpg.Connection = type("Connection", (), {})
    asyncpg.Record = object
    asyncpg.create_pool = _mk_async("create_pool")
    asyncpg.exceptions = types.SimpleNamespace(
        UniqueViolationError=type("UniqueViolationError", (Exception,), {}),
        PostgresError=type("PostgresError", (Exception,), {}),
    )
    sys.modules["asyncpg"] = asyncpg

    pgv = types.ModuleType("pgvector")
    pgv_pg = types.ModuleType("pgvector.asyncpg")
    pgv_pg.register_vector = lambda pool: None
    pgv_pg.register_vector_resources = lambda conn: None
    pgv.asyncpg = pgv_pg
    sys.modules["pgvector"] = pgv
    sys.modules["pgvector.asyncpg"] = pgv_pg

    litellm = types.ModuleType("litellm")
    litellm.acompletion = _mk_async("acompletion")
    litellm.completion = lambda *a, **k: None
    litellm.drop_params = True
    litellm.num_retries = 2
    sys.modules["litellm"] = litellm

    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    gtypes = types.ModuleType("google.genai.types")

    class _Client:
        def __init__(self, *a, **k):
            # deliberately no .aio -> engine.memory.embed raises immediately,
            # ingest_evidence catches it, embedding=None (best-effort contract)
            self.models = types.SimpleNamespace()

    genai.Client = _Client
    genai.types = gtypes
    google.genai = genai
    sys.modules["google"] = google
    sys.modules["google.genai"] = genai
    sys.modules["google.genai.types"] = gtypes

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = dotenv


install_stubs()

# --------------------------------------------------------------------------
# fake pool — migration 012/013/015 column layouts
# --------------------------------------------------------------------------


class FakeConn:
    def __init__(self, store):
        self.store = store

    # -- writes -----------------------------------------------------------
    async def execute(self, sql, *args):
        s = " ".join(sql.split()).upper()
        st = self.store
        if s.startswith("INSERT INTO CASES"):
            # 012 + 015 layout: $1 case_id, $2 title, $3 objective,
            # $4 deadline, $5 required_confidence, $6 tool_calls,
            # $7 tokens, $8 case_type, $9 created_at
            st["cases"][args[0]] = {
                "case_id": args[0], "title": args[1], "objective": args[2],
                "decision_deadline": args[3], "required_confidence": args[4],
                "cost_budget_tool_calls": args[5],
                "cost_budget_tokens": args[6], "case_type": args[7],
                "status": "open", "created_at": args[8], "updated_at": args[8],
            }
        elif s.startswith("INSERT INTO SOURCES"):
            # $1 source_id, $2 case_id, $3 source_type, $4 source_uri,
            # $5 source_hash, $6 parent_source_id, $7 lineage_id,
            # $8 metadata(json str), $9 authority, $10 directness,
            # $11 freshness, $12 created_at
            st["sources"][args[0]] = {
                "source_id": args[0], "case_id": args[1],
                "source_type": args[2], "source_uri": args[3],
                "source_hash": args[4], "parent_source_id": args[5],
                "lineage_id": args[6],
                "metadata": json.loads(args[7]) if isinstance(args[7], str)
                else (args[7] or {}),
                "authority_score": args[8], "directness_score": args[9],
                "freshness_score": args[10], "created_at": args[11],
                "duplicate_cluster_id": None, "independence_score": 0.5,
            }
        elif s.startswith("INSERT INTO EVIDENCE_GRAPH_EDGES"):
            # NOTE: must be matched BEFORE "INSERT INTO EVIDENCE" — the
            # edge-table statement's prefix contains it.
            # $1 edge_id, $2 case_id, $3 from_node, $4 from_type,
            # $5 to_node, $6 to_type, $7 relation, $8 weight,
            # $9 metadata(json str), $10 created_at
            st["edges"][args[0]] = {
                "edge_id": args[0], "case_id": args[1], "from_node": args[2],
                "from_type": args[3], "to_node": args[4], "to_type": args[5],
                "relation": args[6], "weight": args[7],
                "metadata": json.loads(args[8]) if isinstance(args[8], str)
                else (args[8] or {}),
                "created_at": args[9],
            }
        elif s.startswith("INSERT INTO EVIDENCE"):
            # $1 evidence_id, $2 case_id, $3 source_id, $4 type, $5 content,
            # $6 embedding, $7 observed_at, $8 recorded_at, $9 valid_from,
            # $10 valid_until, $11 source_lineage_id, $12 duplicate_cluster_id,
            # $13 metadata(json str), $14 quality_score  (+'ACTIVE','{}')
            st["evidence"][args[0]] = {
                "evidence_id": args[0], "case_id": args[1], "source_id": args[2],
                "evidence_type": args[3], "content": args[4],
                "embedding": args[5], "observed_at": args[6],
                "recorded_at": args[7], "valid_from": args[8],
                "valid_until": args[9], "source_lineage_id": args[10],
                "duplicate_cluster_id": args[11],
                "metadata": json.loads(args[12]) if isinstance(args[12], str)
                else (args[12] or {}),
                "quality_score": args[13], "status": "ACTIVE",
                "supersedes": None, "superseded_by": None,
                "extracted_claim_ids": [], "source_llm": None,
            }
        elif s.startswith("INSERT INTO HYPOTHESES"):
            # $1 hypothesis_id, $2 case_id, $3 statement, $4 normalized,
            # $5 current_version_id, $6 created_at, $7 origin, $8 origin_detail
            st["hypotheses"][args[0]] = {
                "hypothesis_id": args[0], "case_id": args[1],
                "statement": args[2], "normalized_statement": args[3],
                "status": "PROPOSED", "current_version_id": args[4],
                "created_at": args[5], "origin": args[6],
                "origin_detail": args[7],
            }
        elif s.startswith("INSERT INTO HYPOTHESIS_VERSIONS"):
            # Two call sites with different bind shapes:
            #   create_hypothesis (7 args): $1 version_id, $2 hypothesis_id,
            #     $3 initial confidence, $4 supporting_claim_ids, $5 reason,
            #     $6 cause_event_id, $7 created_at — version_number literal 1,
            #     prior_confidence literal NULL, the three evidence lists '{}'.
            #   update_hypothesis (13 args): full column list.
            if len(args) == 7:
                st["versions"][args[0]] = {
                    "version_id": args[0], "hypothesis_id": args[1],
                    "version_number": 1, "confidence": args[2],
                    "prior_confidence": None,
                    "supporting_claim_ids": list(args[3] or []),
                    "contradicting_claim_ids": [],
                    "supporting_evidence_added": [],
                    "supporting_evidence_removed": [],
                    "contradicting_evidence_added": [],
                    "reason": args[4], "cause_event_id": args[5],
                    "created_at": args[6],
                }
            else:
                # $1 version_id, $2 hypothesis_id, $3 version_number,
                # $4 confidence, $5 prior_confidence, $6 supporting_claim_ids,
                # $7 contradicting_claim_ids, $8 supp_ev_added,
                # $9 supp_ev_removed, $10 contra_ev_added, $11 reason,
                # $12 cause_event_id, $13 created_at
                st["versions"][args[0]] = {
                    "version_id": args[0], "hypothesis_id": args[1],
                    "version_number": args[2], "confidence": args[3],
                    "prior_confidence": args[4],
                    "supporting_claim_ids": list(args[5] or []),
                    "contradicting_claim_ids": list(args[6] or []),
                    "supporting_evidence_added": list(args[7] or []),
                    "supporting_evidence_removed": list(args[8] or []),
                    "contradicting_evidence_added": list(args[9] or []),
                    "reason": args[10], "cause_event_id": args[11],
                    "created_at": args[12],
                }
        elif s.startswith("INSERT INTO CLAIMS"):
            # $1 claim_id, $2 case_id, $3 statement, $4 claim_type,
            # $5 scope(json str), $6 valid_from, $7 valid_until,
            # $8 supporting_evidence_ids, $9 confidence, $10 now
            st["claims"][args[0]] = {
                "claim_id": args[0], "case_id": args[1],
                "statement": args[2], "claim_type": args[3],
                "scope": json.loads(args[4]) if isinstance(args[4], str)
                else args[4],
                "valid_from": args[5], "valid_until": args[6],
                "supporting_evidence_ids": list(args[7] or []),
                "contradicting_evidence_ids": [],
                "confidence": args[8], "status": "ACTIVE",
                "created_at": args[9], "updated_at": args[9],
            }
        elif s.startswith("INSERT INTO CASE_EVENTS"):
            st["events"].append({
                "event_id": args[0], "case_id": args[1],
                "event_type": args[2], "event_seq": args[3],
                "conversation_turn": args[4], "wall_clock_time": args[5],
                "payload": json.loads(args[6]) if isinstance(args[6], str)
                else (args[6] or {}),
            })
        elif s.startswith("UPDATE HYPOTHESES SET CURRENT_VERSION_ID"):
            st["hypotheses"][args[1]]["current_version_id"] = args[0]
        else:
            raise AssertionError(f"FakeConn.execute: unhandled SQL: {s[:90]}")
        return "OK"

    # -- scalar reads -------------------------------------------------------
    async def fetchval(self, sql, *args):
        s = " ".join(sql.split()).upper()
        if "MAX(EVENT_SEQ)" in s:
            seqs = [e["event_seq"] for e in self.store["events"]
                    if e["case_id"] == args[0]]
            return (max(seqs) + 1) if seqs else 1
        raise AssertionError(f"FakeConn.fetchval: unhandled SQL: {s[:90]}")

    # -- single-row reads ----------------------------------------------------
    async def fetchrow(self, sql, *args):
        s = " ".join(sql.split()).upper()
        st = self.store
        if "FROM CASES WHERE CASE_ID = $1" in s:
            return st["cases"].get(args[0])
        if "FROM SOURCES WHERE SOURCE_ID = $1" in s and "LINEAGE_ID, SOURCE_ID" in s:
            src = st["sources"].get(args[0])
            return ({"lineage_id": src["lineage_id"],
                     "source_id": src["source_id"]} if src else None)
        if "FROM SOURCES WHERE SOURCE_ID = $1" in s:
            return st["sources"].get(args[0])
        if "FROM EVIDENCE WHERE EVIDENCE_ID = $1" in s:
            return st["evidence"].get(args[0])
        if "FROM CLAIMS WHERE CLAIM_ID = $1" in s:
            return st["claims"].get(args[0])
        if "FROM HYPOTHESES WHERE HYPOTHESIS_ID = $1" in s:
            return st["hypotheses"].get(args[0])
        if "FROM HYPOTHESIS_VERSIONS HV" in s:
            # get_current_version: join on hypotheses.current_version_id
            hyp = st["hypotheses"].get(args[0])
            if hyp is None:
                return None
            return st["versions"].get(hyp["current_version_id"])
        if "FROM EVIDENCE_GRAPH_EDGES" in s and "FROM_NODE = $1" in s:
            for e in st["edges"].values():
                if (e["from_node"] == args[0] and e["to_node"] == args[1]
                        and e["relation"] == args[2]):
                    return {"edge_id": e["edge_id"]}
            return None
        if "JOIN EVIDENCE_GRAPH_EDGES E ON E.FROM_NODE = C.CLAIM_ID" in s:
            # recompute claim_rows query (to_node = $1)
            rows = []
            for e in st["edges"].values():
                if (e["to_node"] == args[0] and e["from_type"] == "claim"
                        and e["relation"] in ("SUPPORTS", "CONTRADICTS")):
                    c = st["claims"].get(e["from_node"])
                    if c:
                        rows.append({
                            "claim_id": c["claim_id"],
                            "confidence": c["confidence"],
                            "supporting_evidence_ids":
                                c["supporting_evidence_ids"],
                            "contradicting_evidence_ids":
                                c["contradicting_evidence_ids"],
                            "relation": e["relation"],
                        })
            return rows
        raise AssertionError(f"FakeConn.fetchrow: unhandled SQL: {s[:90]}")

    # -- multi-row reads -----------------------------------------------------
    async def fetch(self, sql, *args):
        s = " ".join(sql.split()).upper()
        st = self.store
        if "JOIN EVIDENCE_GRAPH_EDGES E ON E.FROM_NODE = C.CLAIM_ID" in s:
            return await self.fetchrow(sql, *args)
        if ("FROM EVIDENCE_GRAPH_EDGES E" in s
                and "FROM_TYPE = 'EVIDENCE'" in s):
            # recompute direct_ev_rows query (to_node = $1)
            return [{
                "evidence_id": e["from_node"],
                "relation": e["relation"],
                "weight": e["weight"],
            } for e in st["edges"].values()
                if e["to_node"] == args[0] and e["from_type"] == "evidence"
                and e["relation"] in ("SUPPORTS", "CONTRADICTS")]
        if "SOURCE_LINEAGE_ID FROM EVIDENCE" in s and "ANY($1" in s:
            return [{"evidence_id": ev["evidence_id"],
                     "source_lineage_id": ev["source_lineage_id"]}
                    for ev in st["evidence"].values()
                    if ev["evidence_id"] in list(args[0])]
        raise AssertionError(f"FakeConn.fetch: unhandled SQL: {s[:90]}")

    def transaction(self):
        return _Tx()


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _CM:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self):
        self.store = {"cases": {}, "sources": {}, "evidence": {},
                      "hypotheses": {}, "versions": {}, "claims": {},
                      "edges": {}, "events": []}

    def acquire(self):
        return _CM(FakeConn(self.store))

    async def close(self):
        pass


# --------------------------------------------------------------------------
# wire the fake pool, then run the doc's P2.5 test verbatim
# --------------------------------------------------------------------------
import db.connection as dbc  # noqa: E402
pool = FakePool()
dbc._pool = pool

from case_engine.case import create_case  # noqa: E402
from case_engine.evidence import _create_source, ingest_evidence  # noqa: E402
from case_engine.hypothesis import (  # noqa: E402
    create_hypothesis, get_current_version, recompute_hypothesis_confidence,
)
from case_engine.evidence_graph import add_edge, EdgeRelation  # noqa: E402
from case_engine.claim import create_claim  # noqa: E402


async def m():
    case = await create_case("P2 Test", "Verify polarity")
    src = await _create_source(case.case_id, "HUMAN", None)

    # Test 1: direct evidence -> hypothesis SUPPORTS raises confidence
    h1 = await create_hypothesis(case.case_id, "Hypothesis 1", 0.5)
    ev1 = await ingest_evidence(
        case.case_id, "strong supporting evidence for hypothesis 1",
        src.source_id)
    await add_edge(case.case_id, ev1.evidence_id, "evidence",
                   h1.hypothesis_id, "hypothesis", EdgeRelation.SUPPORTS,
                   weight=0.8)
    await recompute_hypothesis_confidence(h1.hypothesis_id, "test1")
    v1 = await get_current_version(h1.hypothesis_id)
    print(f"Test 1 (SUPPORTS direct): 0.5 -> {v1.confidence:.3f} (expect > 0.5)")
    check("P2.5 Test 1: evidence->hypothesis SUPPORTS raises confidence",
          v1.confidence > 0.5, extra=f"{v1.confidence}")

    # Test 2: direct evidence -> hypothesis CONTRADICTS lowers confidence
    h2 = await create_hypothesis(case.case_id, "Hypothesis 2", 0.5)
    ev2 = await ingest_evidence(
        case.case_id, "evidence that contradicts hypothesis 2", src.source_id)
    await add_edge(case.case_id, ev2.evidence_id, "evidence",
                   h2.hypothesis_id, "hypothesis", EdgeRelation.CONTRADICTS,
                   weight=0.8)
    await recompute_hypothesis_confidence(h2.hypothesis_id, "test2")
    v2 = await get_current_version(h2.hypothesis_id)
    print(f"Test 2 (CONTRADICTS direct): 0.5 -> {v2.confidence:.3f} (expect < 0.5)")
    check("P2.5 Test 2: evidence->hypothesis CONTRADICTS lowers confidence",
          v2.confidence < 0.5, extra=f"{v2.confidence}")

    # Test 3: claim -> hypothesis CONTRADICTS must invert polarity
    #          (Correction 6 — the Oct 22 bug raised confidence here)
    h3 = await create_hypothesis(case.case_id, "Hypothesis 3", 0.5)
    ev3 = await ingest_evidence(
        case.case_id, "supporting measurement for claim 3", src.source_id)
    c3 = await create_claim(case.case_id, "Claim that contradicts H3",
                            "OBSERVATION",
                            supporting_evidence_ids=[ev3.evidence_id])
    await add_edge(case.case_id, c3.claim_id, "claim", h3.hypothesis_id,
                   "hypothesis", EdgeRelation.CONTRADICTS)
    await recompute_hypothesis_confidence(h3.hypothesis_id, "test3")
    v3 = await get_current_version(h3.hypothesis_id)
    print(f"Test 3 (claim CONTRADICTS): 0.5 -> {v3.confidence:.3f} (expect < 0.5)")
    check("P2.5 Test 3: claim->hypothesis CONTRADICTS inverts polarity "
          "(Correction 6 fixed)", v3.confidence < 0.5, extra=f"{v3.confidence}")

    # Sandbox-extra assertion (cheap, strengthens the harness): the version
    # rows record the evidence attribution the doc's formula describes.
    check("Test 1 version carries the supporting evidence id",
          ev1.evidence_id in v1.supporting_evidence_added)
    check("Test 3 version carries the inverted (contradicting) evidence id",
          ev3.evidence_id in v3.contradicting_evidence_added)

    print("P2 VERIFIED (all 3 polarity cases correct)"
          if not FAILURES else "P2 FAILED")


asyncio.run(m())

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURES: {FAILURES}")
    sys.exit(1)
print("ALL P2 POLARITY CHECKS PASSED")