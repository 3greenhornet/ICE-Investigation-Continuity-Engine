"""case_engine/research.py — research mode that starts completing the
research PROCESS, not just the decision ladder.

Owner:      Hari ICE "addition and improvements" pass, research phase.
            The document's section G verdict: "Research mode does not yet
            complete the research process… There is no comparable
            end-to-end research workflow that systematically discovers
            sources, expands questions, investigates the resulting gaps
            and produces a fully grounded synthesis." This module is the
            first working slice of that workflow, borrowing the two named
            patterns:

    STORM (perspective-guided question asking):
        discover_perspectives() derives investigative perspectives —
        including ones the user did NOT initially consider — from the
        claim-type mix, evidence source types and a disciplinary lens
        set; generate_research_questions() asks, per perspective, the
        questions the current evidence does not answer, and registers
        them as real information gaps (so the existing gap machinery,
        retrieval and actions take over from there).

    Open Deep Research (supervisor decomposition):
        research_brief() runs the whole pass — perspectives -> questions
        -> gaps -> synthesis outline — and returns the structured state,
        ready for the caller (cycle, API, CLI) to act on.

    Synthesis with citations and uncertainty preserved:
        research_synthesis_outline() separates ESTABLISHED findings
        (observation/measurement claims outside contradictions) from
        CONTESTED ones (inside active contradictions) from
        INTERPRETATIONS (interpretation/assumption/prediction claims),
        and never lets the three mix — the document's requirement that a
        research system "distinguish established findings from plausible
        interpretations" and "recognise when additional research is no
        longer worthwhile" (the marginal-value check is reported, not
        decided: ICE proposes, the user disposes).

Deterministic by default (offline proven); an LLM path can improve the
phrasing of perspectives/questions later — the CONTRACT (perspectives
exist, unanswered questions become gaps, synthesis separates epistemic
tiers) does not change.
"""

from __future__ import annotations

import logging
import re

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

# Disciplinary lenses STORM-style perspective discovery always considers —
# the whole point is perspectives the user did not initially consider.
DEFAULT_LENSES = (
    "practitioner / engineering",
    "empirical / quantitative",
    "skeptical / critic",
    "historical / prior-art",
    "stakeholder / user-impact",
    "scalability / failure-mode",
)

_CLAIM_TYPE_LENSES = {
    "MEASUREMENT": "empirical / quantitative",
    "OBSERVATION": "empirical / quantitative",
    "CORRELATION": "statistical / confounding",
    "TEMPORAL_ASSOCIATION": "temporal / sequencing",
    "CAUSAL_CLAIM": "causal-mechanism",
    "INTERPRETATION": "interpretive / theoretical",
    "PREDICTION": "forecast / testability",
    "ASSUMPTION": "assumption-audit",
    "CLAIM": "factual / sourcing",
}

_EVIDENCE_TYPE_LENSES = {
    "DOCUMENT": "documentary / sourcing",
    "URL": "web / recency",
    "HUMAN_INPUT": "expert-elicitation",
    "METRICS": "operational / monitoring",
    "LOG": "forensic / trace",
    "MODEL_INFERENCE": "model-output reliability",
}

_MAX_GAPS_PER_RUN = 12


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2
    }


# ---------------------------------------------------------------------------
# Perspective discovery (STORM-lite)
# ---------------------------------------------------------------------------


async def discover_perspectives(case_id: str) -> dict:
    """Derive investigative perspectives for a case, beyond the obvious.

    Perspectives come from three places:
      1. the claim-type mix actually present (a case full of CAUSAL_CLAIM
         deserves a causal-mechanism lens);
      2. the evidence source types present (metrics-heavy -> operational);
      3. the default lens set — deliberately ALWAYS included, because the
         value is in perspectives nobody asked for.
    Each perspective reports its coverage: how many claims it already
    speaks to (grounded) vs zero (an unexplored angle — the interesting
    ones).
    """
    from case_engine.claim import get_claims_for_case

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")

    claims = [c for c in await get_claims_for_case(case_id) if c.status == "ACTIVE"]
    async with pool.acquire() as conn:
        ev_rows = await conn.fetch(
            "SELECT evidence_type, count(*) AS n FROM evidence "
            "WHERE case_id = $1 AND status = 'ACTIVE' "
            "GROUP BY evidence_type", case_id,
        )
        case_row = await conn.fetchrow(
            "SELECT title, objective FROM cases WHERE case_id = $1", case_id,
        )
    ev_types = {r["evidence_type"]: int(r["n"]) for r in ev_rows}

    lenses: dict[str, dict] = {}
    for lens in DEFAULT_LENSES:
        lenses[lens] = {"source": "default", "grounded_claims": 0}
    for ct in {c.claim_type for c in claims}:
        lens = _CLAIM_TYPE_LENSES.get(ct)
        if lens:
            # a lens derived from a claim type is grounded by those claims
            # by construction — token overlap with the lens NAME is not a
            # meaningful signal for it
            info = lenses.setdefault(
                lens, {"source": f"claim_type:{ct}", "grounded_claims": 0}
            )
            info["grounded_claims"] += sum(
                1 for c in claims if c.claim_type == ct
            )
            if info["source"] == "default":
                info["source"] = f"default+claim_type:{ct}"
    for et, n in ev_types.items():
        lens = _EVIDENCE_TYPE_LENSES.get(et)
        if lens:
            info = lenses.setdefault(
                lens, {"source": f"evidence_type:{et}", "grounded_claims": 0}
            )
            info["grounded_claims"] = max(info["grounded_claims"], 1)
            if info["source"] == "default":
                info["source"] = f"default+evidence_type:{et}"

    # weak extra grounding for DEFAULT lenses only: does any claim speak
    # the lens's language (token overlap with the lens name)?
    claim_tokens = {c.claim_id: _tokens(c.statement) for c in claims}
    lens_tokens = {
        lens: _tokens(lens) for lens, info in lenses.items()
        if info["source"] == "default"
    }
    for c in claims:
        for lens, lt in lens_tokens.items():
            if claim_tokens[c.claim_id] & lt:
                lenses[lens]["grounded_claims"] += 1

    perspectives = [
        {
            "perspective": lens,
            "why": (
                f"derived from {info['source']}"
                if info["source"] != "default"
                else "default lens — deliberately included because nobody "
                     "has to think of it for it to be considered"
            ),
            "grounded_claims": info["grounded_claims"],
            "status": (
                "unexplored" if info["grounded_claims"] == 0 else "partially_explored"
            ),
        }
        for lens, info in sorted(
            lenses.items(), key=lambda kv: kv[1]["grounded_claims"]
        )
    ]

    return {
        "case_id": case_id,
        "title": case_row["title"] if case_row else None,
        "objective": case_row["objective"] if case_row else None,
        "perspectives": perspectives,
        "summary": {
            "total": len(perspectives),
            "unexplored": sum(
                1 for p in perspectives if p["status"] == "unexplored"
            ),
            "active_claims": len(claims),
            "note": (
                "Unexplored perspectives are the STORM insight: follow-up "
                "questions should come from angles the current evidence "
                "does not speak to at all."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Question generation -> real information gaps
# ---------------------------------------------------------------------------


async def generate_research_questions(
    case_id: str,
    max_gaps: int = _MAX_GAPS_PER_RUN,
    only_unexplored: bool = True,
) -> dict:
    """Ask, per perspective, what the current evidence cannot answer.

    For each perspective (unexplored first), generate candidate questions
    from the case objective + the perspective's lens, check whether any
    ACTIVE claim already answers each question (token overlap — HONEST
    NOTE: heuristic grounding), and register the UNANSWERED ones as
    information gaps via the existing gap machinery so retrieval,
    actions and the cycle can take over. Existing gaps with identical
    needed_evidence are not duplicated.
    """
    from case_engine.claim import get_claims_for_case
    from case_engine.information_gap import create_gap, list_gaps_for_case

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")

    disc = await discover_perspectives(case_id)
    claims = [c for c in await get_claims_for_case(case_id) if c.status == "ACTIVE"]
    existing_gaps = await list_gaps_for_case(case_id)
    existing_gap_texts = {g.needed_evidence.strip().lower() for g in existing_gaps}

    perspectives = disc["perspectives"]
    if only_unexplored:
        selected = [p for p in perspectives if p["status"] == "unexplored"]
    else:
        selected = perspectives
    if not selected:  # nothing unexplored: fall back to all, capped
        selected = perspectives[:3]

    objective = disc.get("objective") or disc.get("title") or "this case"
    obj_tokens = _tokens(objective)

    # Goal-driven questions (CHANGES.md "Goal-driven research"): when the case
    # has active goals, ask about the highest-priority ones instead of the
    # case objective. The external-knowledge phrasing ("techniques, libraries,
    # best practices", "benchmarks") is deliberate: SEARCH actions built from
    # these gaps route to web research, the codebase-phrased ones to RepoIntel.
    from case_engine.goals import list_goals

    goals = [g for g in await list_goals(case_id, include_superseded=False)
             if g["status"] in ("active", "exploring")
             and g["kind"] in ("goal", "requirement", "quality_objective", "milestone", "need")]
    goals.sort(key=lambda g: (g.get("priority") or 9, -int(g["clarity_level"])))
    goal_topics = [g["statement"].rstrip(".") for g in goals[:2]]
    # P1 -> 0.8 ... P5 -> 0.4; unprioritised goals 0.5
    topic_priority = {g["statement"].rstrip("."): (0.9 - 0.1 * g["priority"]) if g.get("priority") else 0.5
                      for g in goals[:2]}
    goal_lens_questions = {
        "practitioner / engineering": [
            "What techniques, libraries or tools best achieve this goal (latest best practices and alternatives): {t}?",
            "Where in the codebase is this implemented and what currently limits it: {t}?",
        ],
        "skeptical / critic": [
            "What evidence shows the current implementation does not yet meet this goal: {t}?",
        ],
        "scalability / failure-mode": [
            "What benchmarks or failure modes are known for approaches to this goal: {t}?",
        ],
        "historical / prior-art": [
            "What prior-art open-source projects or papers already solve this goal: {t}?",
        ],
        "stakeholder / user-impact": [
            "Who is affected if this goal is not met, and how would they notice: {t}?",
        ],
    }

    created, skipped_answered, skipped_dup = [], 0, 0
    for p in selected:
        lens = p["perspective"]
        if goal_topics:
            questions = [
                (q.format(t=t), topic_priority[t])
                for t in goal_topics
                for q in goal_lens_questions.get(lens, [
                    "What would an expert in this lens ask first about this goal: {t}?"])
            ]
        else:
            base = 0.4 if p["status"] == "unexplored" else 0.3
            questions = [
                (f"From the {lens} perspective: what is the current evidence "
                 f"about {objective} NOT able to tell us?", base),
                (f"From the {lens} perspective: what would an expert in this "
                 f"lens ask first about {objective}?", base),
            ]
        for q, q_priority in questions:
            if len(created) >= max_gaps:
                break
            q_tokens = _tokens(q)
            answered = any(
                len(claim_tokens_set & q_tokens) >= max(2, len(q_tokens) // 3)
                for claim_tokens_set in (
                    _tokens(c.statement) for c in claims
                )
            ) if claims else False
            # a question built from the objective is never 'answered' by
            # the objective itself — require claim tokens beyond it
            answered = answered and any(
                len((_tokens(c.statement) - obj_tokens) & q_tokens) >= 2
                for c in claims
            )
            if answered:
                skipped_answered += 1
                continue
            gap_text = (
                f"[{lens}] {q}"
            )
            if gap_text.strip().lower() in existing_gap_texts:
                skipped_dup += 1
                continue
            gap = await create_gap(
                case_id=case_id,
                needed_evidence=gap_text,
                why_needed=(
                    f"Perspective-guided research question (STORM-lite): "
                    f"the {lens} lens has {p['grounded_claims']} grounded "
                    f"claims — evidence does not currently answer this."
                ),
                discriminating_hypothesis_ids=[],
                priority=q_priority,
            )
            created.append(
                {
                    "gap_id": gap.gap_id,
                    "perspective": lens,
                    "question": q,
                    "priority": gap.priority,
                }
            )
        if len(created) >= max_gaps:
            break

    await _emit_event(
        case_id, "research_questions_generated",
        {
            "perspectives_considered": len(selected),
            "gaps_created": [g["gap_id"] for g in created],
            "skipped_answered": skipped_answered,
            "skipped_duplicate": skipped_dup,
            "method": "storm-lite-goal-driven" if goal_topics else "storm-lite-deterministic",
        },
    )

    return {
        "case_id": case_id,
        "perspectives_considered": [p["perspective"] for p in selected],
        "gaps_created": created,
        "skipped": {
            "already_answered": skipped_answered,
            "duplicate_of_existing_gap": skipped_dup,
        },
        "summary": {
            "questions_created": len(created),
            "note": (
                "Unanswered research questions become real information "
                "gaps — the existing gap-priority, retrieval and action "
                "machinery takes over from here. ICE proposes; it does "
                "not auto-execute searches."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Synthesis outline — citations and uncertainty preserved
# ---------------------------------------------------------------------------


async def research_synthesis_outline(case_id: str) -> dict:
    """Build a synthesis outline that separates epistemic tiers.

    ESTABLISHED  — observation/measurement claims not inside any active
                   contradiction (with their supporting evidence ids)
    CONTESTED    — claims inside active contradictions (with the
                   contradiction ids — the disagreement itself is data)
    INTERPRETED  — interpretation/assumption/prediction claims
                   (plausible, NOT established — the document's exact
                   distinction)
    OPEN         — unresolved gaps, i.e. where more research could still
                   change the picture + the marginal-value signal
                   (open gaps vs established claims ratio).
    """
    from case_engine.claim import get_claims_for_case
    from case_engine.contradiction import get_active_contradictions
    from case_engine.information_gap import list_gaps_for_case

    claims = [c for c in await get_claims_for_case(case_id) if c.status == "ACTIVE"]
    contradictions = await get_active_contradictions(case_id)
    gaps = await list_gaps_for_case(case_id)

    contested_ids: set[str] = set()
    for c in contradictions:
        contested_ids.update([x for x in (c.claim_a, c.claim_b) if x])

    established, contested, interpreted = [], [], []
    for c in claims:
        entry = {
            "claim_id": c.claim_id,
            "statement": c.statement,
            "claim_type": c.claim_type,
            "supporting_evidence_ids": c.supporting_evidence_ids,
            "confidence": c.confidence,
        }
        if c.claim_id in contested_ids:
            entry["contradiction_context"] = [
                ct.contradiction_id
                for ct in contradictions
                if c.claim_id in (ct.claim_a, ct.claim_b)
            ]
            contested.append(entry)
        elif c.claim_type in ("INTERPRETATION", "ASSUMPTION", "PREDICTION"):
            interpreted.append(entry)
        else:
            established.append(entry)

    open_gaps = [
        {
            "gap_id": g.gap_id,
            "needed_evidence": g.needed_evidence,
            "priority": g.priority,
        }
        for g in gaps
        if g.resolved_at is None
    ]
    ratio = (
        round(len(open_gaps) / max(1, len(established)), 2)
        if established or open_gaps else 0.0
    )
    marginal_value = (
        "research still valuable" if ratio >= 0.5 else
        "consolidation may be worth more than more research" if established
        else "insufficient basis — gather evidence first"
    )

    return {
        "case_id": case_id,
        "established": established,
        "contested": contested,
        "interpreted": interpreted,
        "open": open_gaps,
        "summary": {
            "established": len(established),
            "contested": len(contested),
            "interpreted": len(interpreted),
            "open_gaps": len(open_gaps),
            "open_to_established_ratio": ratio,
            "marginal_value_signal": marginal_value,
            "note": (
                "Tier separation is the contract: interpretations never "
                "render as established findings, contested claims carry "
                "their contradiction ids, and every entry keeps its "
                "citations."
            ),
        },
    }


# ---------------------------------------------------------------------------
# The end-to-end pass (Open-Deep-Research-style supervisor slice)
# ---------------------------------------------------------------------------


async def research_brief(case_id: str, max_gaps: int = 6) -> dict:
    """One full research pass: perspectives -> questions/gaps -> outline.

    This is the slice of the supervisor pattern that runs INSIDE one
    case without external delegation: decompose the brief (which
    perspectives matter), expand questions (as gaps), and assemble the
    grounded synthesis state. External researcher delegation is the
    natural next step once executors gain web-search providers.
    """
    perspectives = await discover_perspectives(case_id)
    questions = await generate_research_questions(case_id, max_gaps=max_gaps)
    outline = await research_synthesis_outline(case_id)
    return {
        "case_id": case_id,
        "perspectives": perspectives,
        "questions": questions,
        "synthesis": outline,
    }
