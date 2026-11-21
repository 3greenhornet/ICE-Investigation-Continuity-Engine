"""Integration tests for the Evolving Project Model (case_engine/goals.py).

The goal system's contracts from the improvements document:

    * intent nodes carry a KIND (goal/need/constraint/…) and a
      CLARITY LEVEL (1 vague -> 4 defined) — a Level-1 project is valid
      state, not an error;
    * ORIGIN preserves the epistemic category (developer_statement vs
      ice_inference vs ice_proposal vs repo_observation) — and a user
      status change PROMOTES an ice_inference to developer_confirmed
      (never silently the other way);
    * revision = SUPERSEDE chain: the old row stays visible, crossed
      out, with a pointer to its replacement — never deleted;
    * MATURITY above 'scaffolded' requires evidence ids ("a class
      existing does not prove a feature works");
    * INTENT PROFILE reports filled vs open slots — intent discovery
      before any repository exists;
    * DECOMPOSITION proposes open questions with origin=ice_proposal
      (GORE-lite) — proposals, never decisions;
    * RECONCILIATION classifies intent-vs-evidence divergence and can
      only conclude intentional_evolution from a recorded user
      supersede chain — keyword overlap alone must not.
"""
import pytest

from case_engine.case import create_case
from case_engine.evidence import _create_source, ingest_evidence
from case_engine.goals import (
    assess_maturity, connect_goals, create_goal, decompose_goal,
    get_goal, goal_dependency_breadth, intent_profile, latest_maturity,
    list_goal_edges, list_goals, reconcile_goals, set_goal_status,
)


async def _new_case(title: str = "Tutoring platform"):
    return await create_case(title, "Help the developer build it")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_goal_crud_and_clarity_levels(db_pool):
    case = await _new_case()
    g = await create_goal(
        case.case_id, "Help students find tutors and arrange sessions",
        kind="goal", clarity_level=2,
    )
    assert g["goal_id"].startswith("goal_")
    assert g["clarity_level"] == 2
    assert g["clarity"] == "emerging direction"
    assert g["origin"] == "developer_statement"
    assert g["status"] == "active"  # clarity>1 -> active, not exploring

    loaded = await get_goal(g["goal_id"])
    assert loaded["statement"] == "Help students find tutors and arrange sessions"

    listed = await list_goals(case.case_id, kind="goal")
    assert any(x["goal_id"] == g["goal_id"] for x in listed)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_level1_vague_goal_is_valid_state(db_pool):
    """A Level-1 exploratory intention is recorded, not rejected."""
    case = await _new_case("Half-formed idea")
    g = await create_goal(
        case.case_id,
        "I want to build something that helps me manage my knowledge",
        clarity_level=1,
    )
    assert g["clarity_level"] == 1
    assert g["status"] == "exploring"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_supersede_chain_never_deletes(db_pool):
    case = await _new_case()
    v1 = await create_goal(
        case.case_id, "Store sessions in MongoDB", kind="decision",
        clarity_level=3,
    )
    v2 = await create_goal(
        case.case_id, "Store sessions in Postgres",
        kind="decision", clarity_level=4,
        rationale="developer changed direction after spike",
        supersedes=v1["goal_id"],
    )
    assert v2["version"] == 2
    old = await get_goal(v1["goal_id"])
    assert old["status"] == "superseded"
    assert old["superseded_by"] == v2["goal_id"]

    listed = await list_goals(case.case_id)
    assert any(x["goal_id"] == v1["goal_id"] for x in listed)  # still visible
    active = await list_goals(case.case_id, include_superseded=False)
    assert not any(x["goal_id"] == v1["goal_id"] for x in active)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_user_status_change_promotes_ice_inference(db_pool):
    """The origin-promotion rule: user decision converts provisional to
    developer_confirmed; an ice status change must NOT."""
    case = await _new_case()
    inferred = await create_goal(
        case.case_id, "Cancellation may need blocking near session start",
        kind="assumption", origin="ice_inference",
    )
    promoted = await set_goal_status(
        inferred["goal_id"], "active", decided_by="user", note="confirmed"
    )
    assert promoted["origin"] == "developer_confirmed"

    other = await create_goal(
        case.case_id, "Maybe add waitlists", kind="alternative",
        origin="ice_proposal",
    )
    unchanged = await set_goal_status(
        other["goal_id"], "abandoned", decided_by="ice"
    )
    assert unchanged["origin"] == "ice_proposal"  # no self-promotion


@pytest.mark.integration
@pytest.mark.asyncio
async def test_maturity_requires_evidence_above_scaffolded(db_pool):
    case = await _new_case()
    g = await create_goal(case.case_id, "Persistent memory", kind="need")

    with pytest.raises(ValueError, match="evidence_ids"):
        await assess_maturity(g["goal_id"], "tested")

    ok = await assess_maturity(g["goal_id"], "no_candidate", note="start")
    assert ok["maturity"] == "no_candidate"
    scaffolded = await assess_maturity(g["goal_id"], "scaffolded")
    assert scaffolded["maturity"] == "scaffolded"

    src = await _create_source(case.case_id, "HUMAN", None)
    ev = await ingest_evidence(
        case.case_id,
        "memory module implemented with postgres table and tests passing",
        src.source_id, "HUMAN_ASSERTION",
    )
    backed = await assess_maturity(
        g["goal_id"], "tested", evidence_ids=[ev.evidence_id]
    )
    assert backed["evidence_ids"] == [ev.evidence_id]

    latest = await latest_maturity(g["goal_id"])
    assert latest["maturity"] == "tested"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_typed_edges_and_dependency_breadth(db_pool):
    case = await _new_case()
    goal = await create_goal(case.case_id, "Reliable session booking")
    sub = await create_goal(case.case_id, "Booking cancellation", kind="goal")
    risk = await create_goal(case.case_id, "Double-booking", kind="risk")

    e1 = await connect_goals(
        sub["goal_id"], goal["goal_id"], "refines", provenance="ice_proposal"
    )
    e2 = await connect_goals(
        risk["goal_id"], sub["goal_id"], "challenges", provenance="ice_inference"
    )
    edges = await list_goal_edges(case.case_id)
    assert {e["edge_id"] for e in edges} >= {e1["edge_id"], e2["edge_id"]}

    breadth = await goal_dependency_breadth(goal["goal_id"])
    assert any(
        d["from_goal"] == sub["goal_id"] for d in breadth["dependents"]
    )
    breadth_sub = await goal_dependency_breadth(sub["goal_id"])
    assert any(
        d["to_goal"] == goal["goal_id"] for d in breadth_sub["depends_on"]
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_intent_profile_reports_open_slots(db_pool):
    """Before a repository exists the profile is mostly open slots — and
    says so, with the questions to ask next."""
    case = await _new_case("Vague idea")
    profile = await intent_profile(case.case_id)
    assert profile["summary"]["slots_filled"] == "0/8"

    await create_goal(
        case.case_id, "Students cannot find suitable tutors today",
        kind="need", clarity_level=2,
    )
    await create_goal(
        case.case_id, "Must run on a laptop", kind="constraint"
    )
    profile2 = await intent_profile(case.case_id)
    assert profile2["summary"]["slots_filled"] == "2/8"
    filled_slots = {s["slot"] for s in profile2["slots"] if s["filled"]}
    assert "problem" in filled_slots and "constraints" in filled_slots
    open_slots = {s["slot"] for s in profile2["slots"] if not s["filled"]}
    assert "open_decisions" in open_slots


@pytest.mark.integration
@pytest.mark.asyncio
async def test_decompose_proposes_never_decides(db_pool):
    case = await _new_case()
    g = await create_goal(case.case_id, "Support booking cancellation")
    result = await decompose_goal(g["goal_id"])
    assert len(result["proposed_open_questions"]) == 5
    for q in result["proposed_open_questions"]:
        assert q["kind"] == "open_question"
        assert q["origin"] == "ice_proposal"  # GORE-lite contract
    edges = await list_goal_edges(case.case_id)
    refine_edges = [
        e for e in edges if e["relation"] == "refines"
        and e["to_goal"] == g["goal_id"]
    ]
    assert len(refine_edges) == 5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconcile_honest_classification(db_pool):
    """Keyword evidence alone can NEVER produce intentional_evolution —
    only a user-recorded supersede chain can."""
    case = await _new_case()
    g = await create_goal(
        case.case_id, "Sessions must be cancellable", kind="need",
        clarity_level=3,
    )
    src = await _create_source(case.case_id, "HUMAN", None)
    await ingest_evidence(
        case.case_id,
        "sessions cancellable: cancellation endpoint implemented and tested",
        src.source_id, "HUMAN_ASSERTION",
    )

    rep = await reconcile_goals(case.case_id)
    entry = next(p for p in rep["per_goal"] if p["goal_id"] == g["goal_id"])
    assert entry["classification"] == "aligned"  # evidence supports it

    # no evidence for this one -> unresolved/not_evidenced, never a guess
    g2 = await create_goal(case.case_id, "Waitlist support", kind="need")
    rep2 = await reconcile_goals(case.case_id)
    entry2 = next(p for p in rep2["per_goal"] if p["goal_id"] == g2["goal_id"])
    assert entry2["classification"] in ("not_evidenced", "unresolved")

    # user changes direction: old goal superseded by a developer-authored
    # statement — that chain IS an intentional evolution (the only thing
    # that can be), and the superseded goal leaves the active set
    g3 = await create_goal(
        case.case_id, "Waitlist handled by partner tool", kind="need",
        clarity_level=3, supersedes=g2["goal_id"],
    )
    rep3 = await reconcile_goals(case.case_id)
    entry3 = next(
        p for p in rep3["per_goal"] if p["goal_id"] == g3["goal_id"]
    )
    # the superseded goal disappears from active reconciliation…
    assert not any(
        p["goal_id"] == g2["goal_id"] for p in rep3["per_goal"]
    )
    # …and the active goal of the user chain is exactly intentional
    # evolution — keyword evidence was never needed to say so
    assert entry3["classification"] == "intentional_evolution"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconcile_intentional_evolution_requires_user_chain(db_pool):
    case = await _new_case()
    v1 = await create_goal(
        case.case_id, "Use MongoDB for sessions", kind="decision",
        clarity_level=3, origin="developer_statement",
    )
    # evidence of the OLD direction exists; user then supersedes it
    src = await _create_source(case.case_id, "HUMAN", None)
    await ingest_evidence(
        case.case_id,
        "sessions mongodb decision revised by developer after spike",
        src.source_id, "HUMAN_ASSERTION",
    )
    v2 = await create_goal(
        case.case_id, "Use Postgres for sessions", kind="decision",
        clarity_level=4, origin="developer_statement",
        supersedes=v1["goal_id"],
    )
    rep = await reconcile_goals(case.case_id)
    # the superseded goal left the active set…
    assert not any(
        p["goal_id"] == v1["goal_id"] for p in rep["per_goal"]
    )
    # …and the ACTIVE goal of the user-driven chain is the one thing that
    # legitimately counts as intentional evolution
    entry = next(p for p in rep["per_goal"] if p["goal_id"] == v2["goal_id"])
    assert entry["classification"] == "intentional_evolution"
    assert rep["summary"]["intentional_evolutions"] == 1
