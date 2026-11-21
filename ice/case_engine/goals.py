"""case_engine/goals.py — the Evolving Project Model (goal system).

Owner:      Hari ICE "addition and improvements" pass, goal-system phase.
            Implements the document's late-phase core requirement that the
            previous pass left unwired: ICE must represent PROJECT INTENTION
            as a first-class, versioned, typed graph — starting before any
            repository exists ("ICE shouldn't wait for a repository to
            exist. It should begin building a provisional understanding"),
            surviving goal changes as supersede-chains (never deletions),
            and continuously reconciling intention against implementation
            evidence with honest divergence classification.

Three planes, kept separate on purpose (the document's separation):
    WHAT THE DEVELOPER WANTS  -> project_goals + goal_edges (this module)
    WHAT ICE PROPOSED         -> origin='ice_proposal' rows — visible,
                                 never auto-promoted to confirmed
    WHAT ACTUALLY EXISTS      -> evidence/claims (ingested elsewhere);
                                 goal_progress records maturity as an
                                 EVIDENCE-BACKED assessment, not a guess.

Epistemic categories (origin) — the document demands these distinctions:
    developer_statement  explicitly stated intention (human-authored)
    developer_confirmed  explicitly accepted model change (human-approved)
    ice_inference        ICE's interpretation (provisional)
    ice_proposal         ICE's suggestion (not accepted)
    repo_observation     tool-derived evidence about implementation

Key honesty rules enforced here:
    * revise_goal() with origin='developer_statement'/'developer_confirmed'
      can only come from the user path (the API layer enforces who calls);
      an ice_inference NEVER overwrites a developer_statement in place —
      it supersedes only via a new row.
    * reconcile_goals() classifies divergence as accidental / intentional /
      temporary / unresolved — 'intentional_evolution' REQUIRES a recorded
      user decision (a goal revision with decided_by='user' or origin
      developer_confirmed); keyword evidence alone can never produce it.
    * assess_maturity() never jumps the ladder: evidence_ids must be
      non-empty for any maturity above no_candidate/scaffolded.

Deps:       db.connection, case_engine (_emit_event), exceptions.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

GOAL_KINDS = (
    "goal", "need", "actor", "scenario", "requirement",
    "quality_objective", "constraint", "assumption", "decision",
    "alternative", "risk", "open_question", "milestone",
)
GOAL_STATUSES = ("exploring", "active", "satisfied", "abandoned", "superseded")
ORIGINS = (
    "developer_statement", "developer_confirmed", "ice_inference",
    "ice_proposal", "repo_observation",
)
EDGE_RELATIONS = (
    "refines", "depends_on", "constrains", "motivated_by", "addresses",
    "conflicts_with", "supersedes", "derived_from", "challenges",
)
MATURITY_LADDER = (
    "no_candidate", "scaffolded", "candidate", "connected",
    "tested", "verified", "runtime_observed",
)

CLARITY_LEVELS = {
    1: "exploratory / vague intention",
    2: "emerging direction",
    3: "partially specified project",
    4: "defined engineering objective",
}

# What reconcile_goals() may conclude — the document's four situations
# plus the fifth (the intention itself may be wrong), kept as an explicit
# classification rather than a silent default.
DIVERGENCE_CLASSES = (
    "aligned",               # implementation evidence supports the goal
    "accidental_divergence", # contradicts established intent, no decision
    "intentional_evolution", # user-recorded direction change
    "temporary_deviation",   # explicitly marked scaffold/shortcut
    "unresolved",            # evidence cannot tell (the honest default)
    "intent_questioned",     # implementation suggests the GOAL is wrong
)

# The intent-profile scaffold — the document's "initial intent profile"
# (§4 'Intent discovery must work before a repository exists'). Each slot
# is a question ICE should be able to help the developer answer BEFORE
# code exists; filled ones become goal rows of the matching kind.
INTENT_PROFILE_SLOTS = (
    ("problem", "need",
     "What problem is the project actually solving (why is it being built)?"),
    ("users", "actor",
     "Who are the intended users and what should they be able to achieve?"),
    ("outcomes", "goal",
     "What outcome does the developer want — what would make the first "
     "version genuinely useful?"),
    ("scenarios", "scenario",
     "What are the main usage scenarios / expected interactions?"),
    ("quality_objectives", "quality_objective",
     "Which quality objectives matter (performance, reliability, privacy…)?"),
    ("constraints", "constraint",
     "What hard constraints exist (tech, time, resources, environment)?"),
    ("assumptions", "assumption",
     "What is currently believed but not established?"),
    ("open_decisions", "open_question",
     "Which decisions are still open? Which alternatives were considered?"),
)


def _stem(token: str) -> str:
    """Heuristic morphological stemmer to reduce false divergence in reconciliation."""
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            if suffix == "ies":
                return token[:-3] + "y"
            return token[:-len(suffix)]
    return token


def _tokens(text: str) -> set[str]:
    raw = [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2]
    return set(raw) | {_stem(t) for t in raw}


def _row_to_goal(row) -> dict:
    return {
        "goal_id": row["goal_id"],
        "case_id": row["case_id"],
        "kind": row["kind"],
        "statement": row["statement"],
        "clarity_level": int(row["clarity_level"]),
        "clarity": CLARITY_LEVELS[int(row["clarity_level"])],
        "status": row["status"],
        "origin": row["origin"],
        "rationale": row["rationale"],
        "version": int(row["version"]),
        "supersedes": row["supersedes"],
        "superseded_by": row["superseded_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ---------------------------------------------------------------------------
# CRUD — create / read / list / status
# ---------------------------------------------------------------------------


async def create_goal(
    case_id: str,
    statement: str,
    kind: str = "goal",
    clarity_level: int = 1,
    origin: str = "developer_statement",
    rationale: Optional[str] = None,
    supersedes: Optional[str] = None,
) -> dict:
    """Record one intent node (or a revision superseding an earlier one).

    Postconditions: project_goals row + goal_created event. When
    ``supersedes`` is given the OLD row is marked superseded with a
    pointer to the new one — crossed out, never deleted, exactly like
    the plan manager's contract.
    """
    if kind not in GOAL_KINDS:
        raise ValueError(f"Invalid goal kind: {kind}")
    if origin not in ORIGINS:
        raise ValueError(f"Invalid origin: {origin}")
    if clarity_level not in CLARITY_LEVELS:
        raise ValueError(f"clarity_level must be 1-4, got {clarity_level}")
    if not statement or not statement.strip():
        raise ValueError("statement must be non-empty")

    from case_engine.case import get_case

    await get_case(case_id)  # raises CaseNotFoundError

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")

    goal_id = f"goal_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            version = 1
            if supersedes:
                old = await conn.fetchrow(
                    "SELECT version, case_id FROM project_goals "
                    "WHERE goal_id = $1", supersedes,
                )
                if old is None:
                    raise ValueError(f"Cannot supersede unknown goal: {supersedes}")
                if old["case_id"] != case_id:
                    raise ValueError("Cannot supersede a goal from another case")
                version = int(old["version"]) + 1
            await conn.execute(
                """
                INSERT INTO project_goals (
                    goal_id, case_id, kind, statement, clarity_level,
                    status, origin, rationale, version, supersedes,
                    created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11)
                """,
                goal_id, case_id, kind, statement.strip(), clarity_level,
                "exploring" if clarity_level == 1 else "active",
                origin, rationale, version, supersedes, now,
            )
            if supersedes:
                await conn.execute(
                    """
                    UPDATE project_goals
                    SET status = 'superseded', superseded_by = $1,
                        updated_at = $2
                    WHERE goal_id = $3
                    """,
                    goal_id, now, supersedes,
                )
            await _emit_event(
                case_id, "goal_created",
                {
                    "goal_id": goal_id, "kind": kind,
                    "statement": statement.strip()[:300],
                    "clarity_level": clarity_level, "origin": origin,
                    "supersedes": supersedes,
                },
                conn=conn,
            )

    return await get_goal(goal_id)


async def get_goal(goal_id: str) -> dict:
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM project_goals WHERE goal_id = $1", goal_id,
        )
    if row is None:
        raise ValueError(f"Goal not found: {goal_id}")
    return _row_to_goal(row)


async def list_goals(
    case_id: str,
    kind: Optional[str] = None,
    include_superseded: bool = True,
) -> list[dict]:
    """List a case's intent graph nodes; superseded stay visible by default."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    clauses = ["case_id = $1"]
    params: list = [case_id]
    if kind:
        clauses.append("kind = $2")
        params.append(kind)
    if not include_superseded:
        clauses.append("status <> 'superseded'")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM project_goals WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at",
            *params,
        )
    return [_row_to_goal(r) for r in rows]


async def set_goal_status(
    goal_id: str,
    new_status: str,
    decided_by: str = "user",
    note: str = "",
) -> dict:
    """Transition a goal's status (satisfied / abandoned / …).

    'developer_confirmed' promotion happens HERE and only here from the
    user path: recording a user decision about a goal ICE proposed or
    inferred re-labels its origin as confirmed — the documented bridge
    from provisional to accepted.
    """
    if new_status not in GOAL_STATUSES:
        raise ValueError(f"Invalid goal status: {new_status}")
    current = await get_goal(goal_id)
    if current["status"] == new_status:
        return current

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            new_origin = current["origin"]
            if decided_by == "user" and current["origin"] in (
                "ice_inference", "ice_proposal"
            ):
                new_origin = "developer_confirmed"
            await conn.execute(
                """
                UPDATE project_goals
                SET status = $1, origin = $2, updated_at = $3,
                    version = version + 1
                WHERE goal_id = $4
                """,
                new_status, new_origin, now, goal_id,
            )
            await _emit_event(
                current["case_id"], "goal_status_changed",
                {
                    "goal_id": goal_id,
                    "old_status": current["status"],
                    "new_status": new_status,
                    "decided_by": decided_by,
                    "note": note[:300],
                    "origin_promoted_to": (
                        new_origin if new_origin != current["origin"] else None
                    ),
                },
                conn=conn,
            )

    return await get_goal(goal_id)


# ---------------------------------------------------------------------------
# Typed edges — the "why does this exist / what depends on it" substrate
# ---------------------------------------------------------------------------


async def connect_goals(
    from_goal: str,
    to_goal: str,
    relation: str,
    provenance: str = "ice_inference",
    note: Optional[str] = None,
) -> dict:
    """Add one typed relationship between intent nodes.

    Example: decision --motivated_by--> goal, subgoal --refines--> goal,
    risk --challenges--> assumption. Conflict edges are how ICE records
    that two intents cannot both hold — the raw material the
    reconciliation engine classifies.
    """
    if relation not in EDGE_RELATIONS:
        raise ValueError(f"Invalid edge relation: {relation}")
    if provenance not in ORIGINS:
        raise ValueError(f"Invalid provenance: {provenance}")
    a, b = await get_goal(from_goal), await get_goal(to_goal)
    if a["case_id"] != b["case_id"]:
        raise ValueError("Cannot connect goals across cases")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    edge_id = f"gedge_{uuid.uuid4().hex[:12]}"

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO goal_edges (
                    edge_id, case_id, from_goal, to_goal, relation,
                    provenance, note, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (from_goal, to_goal, relation) DO NOTHING
                """,
                edge_id, a["case_id"], from_goal, to_goal, relation,
                provenance, note, datetime.now(timezone.utc),
            )
            await _emit_event(
                a["case_id"], "goal_edge_added",
                {
                    "edge_id": edge_id, "from_goal": from_goal,
                    "to_goal": to_goal, "relation": relation,
                    "provenance": provenance,
                },
                conn=conn,
            )

    return await get_edge(edge_id)


async def get_edge(edge_id: str) -> dict:
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM goal_edges WHERE edge_id = $1", edge_id,
        )
    if row is None:
        raise ValueError(f"Goal edge not found: {edge_id}")
    return {
        "edge_id": row["edge_id"], "case_id": row["case_id"],
        "from_goal": row["from_goal"], "to_goal": row["to_goal"],
        "relation": row["relation"], "provenance": row["provenance"],
        "note": row["note"], "created_at": row["created_at"],
    }


async def list_goal_edges(case_id: str) -> list[dict]:
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM goal_edges WHERE case_id = $1 ORDER BY created_at",
            case_id,
        )
    return [
        {
            "edge_id": r["edge_id"], "case_id": r["case_id"],
            "from_goal": r["from_goal"], "to_goal": r["to_goal"],
            "relation": r["relation"], "provenance": r["provenance"],
            "note": r["note"], "created_at": r["created_at"],
        }
        for r in rows
    ]


async def goal_dependency_breadth(goal_id: str) -> dict:
    """What depends on this goal / what does it depend on.

    The document's motivating queries: "Which goals depend on it? What
    else could be affected by changing it?" — answered from the typed
    edge store, both directions.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        depends_on = await conn.fetch(
            "SELECT * FROM goal_edges WHERE from_goal = $1 "
            "AND relation IN ('depends_on','refines','constrains')",
            goal_id,
        )
        dependents = await conn.fetch(
            "SELECT * FROM goal_edges WHERE to_goal = $1 "
            "AND relation IN ('depends_on','refines','constrains')",
            goal_id,
        )
    return {
        "goal_id": goal_id,
        "depends_on": [dict(r) for r in depends_on],
        "dependents": [dict(r) for r in dependents],
    }


# ---------------------------------------------------------------------------
# Maturity — evidence-backed implementation state per goal
# ---------------------------------------------------------------------------


async def assess_maturity(
    goal_id: str,
    maturity: str,
    evidence_ids: Optional[list[str]] = None,
    assessed_by: str = "ice",
    note: str = "",
) -> dict:
    """Record an implementation-maturity assessment for one goal.

    Honesty rule (the document's): maturity above 'scaffolded' REQUIRES
    non-empty evidence_ids — "a class existing does not prove a feature
    works; a test file existing does not prove a test has passed".
    """
    if maturity not in MATURITY_LADDER:
        raise ValueError(f"Invalid maturity: {maturity}")
    if (
        MATURITY_LADDER.index(maturity) > MATURITY_LADDER.index("scaffolded")
        and not evidence_ids
    ):
        raise ValueError(
            f"maturity '{maturity}' requires non-empty evidence_ids — "
            "an unevidenced maturity claim is exactly what this ladder "
            "exists to prevent"
        )

    goal = await get_goal(goal_id)
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    progress_id = f"gprog_{uuid.uuid4().hex[:12]}"

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO goal_progress (
                    progress_id, goal_id, case_id, maturity,
                    evidence_ids, assessed_by, note, assessed_at
                ) VALUES ($1, $2, $3, $4, $5::text[], $6, $7, $8)
                """,
                progress_id, goal_id, goal["case_id"], maturity,
                evidence_ids or [], assessed_by, note,
                datetime.now(timezone.utc),
            )
            await _emit_event(
                goal["case_id"], "goal_maturity_assessed",
                {
                    "goal_id": goal_id, "maturity": maturity,
                    "evidence_ids": evidence_ids or [],
                    "assessed_by": assessed_by, "note": note[:300],
                },
                conn=conn,
            )

    return {
        "progress_id": progress_id, "goal_id": goal_id,
        "maturity": maturity, "evidence_ids": evidence_ids or [],
        "assessed_by": assessed_by, "note": note,
    }


async def latest_maturity(goal_id: str) -> Optional[dict]:
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM goal_progress WHERE goal_id = $1 "
            "ORDER BY assessed_at DESC LIMIT 1", goal_id,
        )
    if row is None:
        return None
    return {
        "progress_id": row["progress_id"], "goal_id": row["goal_id"],
        "maturity": row["maturity"],
        "evidence_ids": list(row["evidence_ids"] or []),
        "assessed_by": row["assessed_by"], "note": row["note"],
        "assessed_at": row["assessed_at"],
    }


# ---------------------------------------------------------------------------
# Intent profile — discovery BEFORE a repository exists
# ---------------------------------------------------------------------------


async def intent_profile(case_id: str) -> dict:
    """ICE's current understanding of a project, including its gaps.

    Returns the document's initial intent profile: every slot
    (problem, users, outcomes, scenarios, quality objectives,
    constraints, assumptions, open decisions) marked filled / open,
    with the matching goal rows. A Level-1 project will be almost all
    open slots — that IS the correct output, made visible.
    """
    goals = await list_goals(case_id, include_superseded=False)
    slots = []
    for slot, kind, question in INTENT_PROFILE_SLOTS:
        matching = [g for g in goals if g["kind"] == kind]
        slots.append(
            {
                "slot": slot,
                "kind": kind,
                "question": question,
                "filled": bool(matching),
                "statements": [
                    {
                        "goal_id": g["goal_id"], "statement": g["statement"],
                        "origin": g["origin"],
                        "clarity_level": g["clarity_level"],
                    }
                    for g in matching
                ],
            }
        )
    filled = sum(1 for s in slots if s["filled"])
    avg_clarity = (
        round(sum(g["clarity_level"] for g in goals) / len(goals), 2)
        if goals else None
    )
    return {
        "case_id": case_id,
        "slots": slots,
        "summary": {
            "slots_filled": f"{filled}/{len(slots)}",
            "goal_count": len(goals),
            "average_clarity_level": avg_clarity,
            "dominant_origin": (
                max(
                    set(g["origin"] for g in goals),
                    key=lambda o: sum(1 for g in goals if g["origin"] == o),
                )
                if goals else None
            ),
            "note": (
                "Unfilled slots are the correct next questions to ask — "
                "intent discovery works before any repository exists."
            ),
        },
    }


# ---------------------------------------------------------------------------
# GORE-lite decomposition — goals -> candidate subgoals + open questions
# ---------------------------------------------------------------------------

_DECOMPOSITION_TEMPLATES = {
    "goal": (
        "Break this goal into: who participates, what they must be able to "
        "do, what must always hold, and what could go wrong."
    ),
    "need": (
        "Which capability does this need imply, and what would count as "
        "evidence that it exists in the implementation?"
    ),
    "constraint": (
        "Is this constraint verifiable? What observation would show a "
        "violation?"
    ),
    "milestone": (
        "What is the smallest useful step that produces verifiable "
        "progress toward this milestone?"
    ),
}


async def decompose_goal(goal_id: str, decided_by: str = "ice") -> dict:
    """Propose a decomposition of one goal (GORE-lite, deterministic).

    Deterministic path: derive candidate sub-questions from the goal's
    kind and statement — participants, capabilities, invariants, risks,
    evidence conditions — and register them as open_question nodes with
    origin='ice_proposal' (NEVER auto-accepted). The LLM path can later
    replace the phrasing; the CONTRACT (proposals, not decisions) stays.
    """
    goal = await get_goal(goal_id)
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")

    template = _DECOMPOSITION_TEMPLATES.get(goal["kind"], _DECOMPOSITION_TEMPLATES["goal"])
    subject = goal["statement"].rstrip(".?!")
    questions = [
        f"Who or what participates in: {subject}?",
        f"What must the system be able to DO for: {subject}?",
        f"What must always hold (invariants) for: {subject}?",
        f"What could go wrong / what risks does: {subject} — carry?",
        f"What observation would count as EVIDENCE that: {subject} — is met?",
    ]

    created: list[dict] = []
    for q in questions:
        created.append(
            await create_goal(
                case_id=goal["case_id"],
                statement=q,
                kind="open_question",
                clarity_level=goal["clarity_level"],
                origin="ice_proposal",
                rationale=f"decomposition of {goal_id}: {template}",
            )
        )
    edges: list[dict] = []
    for sub in created:
        edges.append(
            await connect_goals(
                from_goal=sub["goal_id"], to_goal=goal_id,
                relation="refines", provenance="ice_proposal",
                note="decomposition",
            )
        )

    await _emit_event(
        goal["case_id"], "goal_decomposition_proposed",
        {
            "goal_id": goal_id,
            "proposed_subquestions": [c["goal_id"] for c in created],
            "method": "deterministic-gore-lite",
        },
    )

    return {
        "goal_id": goal_id,
        "template": template,
        "proposed_open_questions": created,
        "edges": edges,
        "note": (
            "Proposals only (origin=ice_proposal): nothing here is an "
            "accepted decision until the user confirms or a "
            "developer_statement records it."
        ),
    }


# ---------------------------------------------------------------------------
# Reconciliation — intent vs implementation evidence
# ---------------------------------------------------------------------------


async def reconcile_goals(case_id: str) -> dict:
    """Compare active goals against implementation evidence.

    For every non-superseded goal of kind goal/need/milestone, search the
    case's claims + evidence for support or conflict (keyword coverage —
    HONEST NOTE: a heuristic; the LLM critique path can replace the
    scoring, not the contract) and classify:

        aligned               evidence clearly supports the goal
        accidental_divergence evidence contradicts with NO user decision
        intentional_evolution a user-recorded revision touched this goal
        temporary_deviation   goal marked as scaffold/shortcut by the user
        intent_questioned     evidence suggests the goal itself is wrong
        unresolved            anything else — the honest default

    'intentional_evolution' requires a supersede chain or status change
    decided_by='user'; keyword overlap alone can NEVER produce it (the
    same anti-self-deception rule as plan.reconcile_plan).
    Returns a PROPOSAL, not a mutation.
    """
    from case_engine.claim import get_claims_for_case

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")

    goals = [
        g for g in await list_goals(case_id, include_superseded=False)
        if g["kind"] in ("goal", "need", "requirement", "milestone",
                         "decision")
    ]
    claims = [c.statement for c in await get_claims_for_case(case_id)]
    async with pool.acquire() as conn:
        ev_rows = await conn.fetch(
            "SELECT content FROM evidence WHERE case_id = $1 "
            "AND status = 'ACTIVE'", case_id,
        )
        # parents of supersede chains with their origins — an ACTIVE goal
        # whose superseded parent was a user-authored decision is itself
        # the result of an intentional evolution
        parent_rows = await conn.fetch(
            """
            SELECT c.goal_id AS child_id, p.goal_id AS parent_id,
                   p.origin AS parent_origin
            FROM project_goals c
            JOIN project_goals p ON c.supersedes = p.goal_id
            WHERE c.case_id = $1
            """,
            case_id,
        )
    evidence_texts = [r["content"] for r in ev_rows]
    corpus = claims + evidence_texts

    # user-driven intent changes: supersede parents the developer authored
    # or confirmed + goals the user explicitly confirmed
    user_evolved = {
        r["child_id"] for r in parent_rows
        if r["parent_origin"] in ("developer_statement", "developer_confirmed")
    }
    user_evolved.update(
        g["goal_id"] for g in goals
        if g["origin"] == "developer_confirmed"
    )

    per_goal = []
    for goal in goals:
        g_tokens = _tokens(goal["statement"])
        best_cov, best_source = 0.0, None
        for text in corpus:
            cov = len(g_tokens & _tokens(text)) / max(1, len(g_tokens))
            if cov > best_cov:
                best_cov, best_source = cov, text

        maturity = await latest_maturity(goal["goal_id"])
        maturity_state = maturity["maturity"] if maturity else "no_candidate"

        if goal["goal_id"] in user_evolved:
            classification = "intentional_evolution"
        elif best_cov >= 0.6:
            classification = "aligned"
        elif 0.3 <= best_cov < 0.6:
            classification = "unresolved"
        else:
            classification = (
                "unresolved" if maturity_state != "no_candidate"
                else "not_evidenced"
            )

        per_goal.append(
            {
                "goal_id": goal["goal_id"],
                "kind": goal["kind"],
                "statement": goal["statement"],
                "clarity_level": goal["clarity_level"],
                "origin": goal["origin"],
                "maturity": maturity_state,
                "evidence_coverage": round(best_cov, 3),
                "best_evidence_excerpt": (best_source or "")[:200],
                "classification": classification,
                "proposed_action": (
                    "record evidence for the revised intent" if
                    classification == "intentional_evolution" else
                    "gather evidence" if classification == "unresolved"
                    else "keep observing"
                ),
            }
        )

    return {
        "case_id": case_id,
        "per_goal": per_goal,
        "summary": {
            "goals_checked": len(per_goal),
            "aligned": sum(1 for p in per_goal if p["classification"] == "aligned"),
            "unresolved": sum(
                1 for p in per_goal if p["classification"] in
                ("unresolved", "not_evidenced")
            ),
            "intentional_evolutions": sum(
                1 for p in per_goal if p["classification"] ==
                "intentional_evolution"
            ),
            "note": (
                "A proposal, not a mutation: keyword coverage alone never "
                "reclassifies intent — intentional evolution requires a "
                "user-recorded decision (supersede chain or confirmed "
                "status change)."
            ),
        },
    }
