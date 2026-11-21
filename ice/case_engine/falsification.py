"""case_engine/falsification.py — two-stage falsification loop.

Owner:      ICE revamp, Part 3 §9.
Purpose:    Generate candidate falsification factors for a hypothesis via
            LLM, filter them for relevance/feasibility/strength, create
            FIND_COUNTEREVIDENCE actions for the survivors, and check whether
            contradicting evidence has falsified a hypothesis.
Deps:       case_engine.schemas, case_engine.hypothesis (including the
            internal _evidence_weight — allowed within the module group per
            the Part 3 §12 note), case_engine.llm, case_engine.action,
            case_engine.evidence_graph.

Part 3 §0 rule 13: falsification is gated behind ENABLE_FALSIFICATION_LOOP
(imported from case_engine.action, the flag's definition site). When the
flag is disabled, the LLM-touching stages return their stubs ([]) without
any model call, so the P0 build runs end-to-end without this subsystem.
check_hypothesis_falsified is pure evidence arithmetic and stays available.
"""

import logging
from typing import Optional

from case_engine.schemas import Action
from case_engine.hypothesis import (
    get_current_version,
    set_hypothesis_status,
    get_hypothesis,
    _evidence_weight,  # internal, allowed within module group (Part 3 §12)
)
from case_engine.llm import _call_llm_json
from case_engine.action import create_action, ENABLE_FALSIFICATION_LOOP
from case_engine.evidence_graph import (
    get_supporting_evidence,
    get_contradicting_evidence,
)

logger = logging.getLogger(__name__)

FALSIFICATION_GENERATION_PROMPT = '''You are designing a falsification test for a hypothesis.

HYPOTHESIS: {hypothesis_statement}
CURRENT CONFIDENCE: {confidence}
SUPPORTING EVIDENCE:
{supporting_summary}

Generate {n} specific falsification factors. Each factor is a piece of evidence that, if found, would significantly weaken the hypothesis.

For each factor:
- Describe the evidence concretely (not abstractly)
- Specify the source type (log, metric, document, human observation)
- Explain why finding it would falsify the hypothesis

Rules:
- Be specific. Not "evidence that contradicts" but "a latency spike in region B where no deployment occurred"
- Do NOT generate vague factors like "any contrary evidence"
- Each factor should be independently testable

Return ONLY a JSON object:
{{"candidates": [{{"description": "...", "source_type": "...", "why_falsifies": "..."}}]}}
'''

FALSIFICATION_FILTER_PROMPT = '''You are filtering falsification factors for practical relevance.

HYPOTHESIS: {hypothesis_statement}

CANDIDATE FALSIFICATION FACTORS:
{candidates}

For each factor, rate:
- relevance: 0-1, how directly it would test the hypothesis
- feasibility: 0-1, how easily this evidence could be obtained
- strength: 0-1, how much it would weaken the hypothesis if found

Reject factors with relevance < 0.5 or feasibility < 0.3.

Return ONLY a JSON object:
{{"filtered": [{{"description": "...", "relevance": 0.8, "feasibility": 0.6, "strength": 0.7}}]}}
'''


async def generate_falsification_candidates(
    hypothesis_id: str,
    max_candidates: int = 5,
) -> list[dict]:
    """Stage 1: generate candidate falsification factors.

    Postconditions:
        Returns a list of dicts with keys: description, source_type,
        why_falsifies (at most max_candidates).
        Returns [] on LLM failure or when ENABLE_FALSIFICATION_LOOP is
        disabled (rule 13 stub).
    """
    if not ENABLE_FALSIFICATION_LOOP:
        return []

    hypothesis = await get_hypothesis(hypothesis_id)
    current = await get_current_version(hypothesis_id)
    supporting = await get_supporting_evidence(hypothesis_id)
    supporting_summary = "\n".join(
        f"- {e.content[:200]}" for e in supporting[:5]
    ) or "(no supporting evidence yet)"

    # Hari ICE improvements pass: prompt centralisation — templates moved to
    # case_engine.prompts (text preserved verbatim), CRITIC role for filter.
    from case_engine.prompts import render as render_prompt
    _pid, _pver, prompt = render_prompt(
        "falsification_generation",
        hypothesis_statement=hypothesis.statement,
        confidence=current.confidence,
        supporting_summary=supporting_summary,
        n=max_candidates,
    )
    raw = await _call_llm_json(
        prompt,
        temperature=0.4,
        max_retries=3,
        role="GENERATOR",
        operation="falsification_generation",
        prompt_id=_pid,
        prompt_version=_pver,
        case_id=hypothesis.case_id,
    )
    if not raw or "candidates" not in raw or not isinstance(raw["candidates"], list):
        return []

    valid: list[dict] = []
    for item in raw["candidates"]:
        if not isinstance(item, dict):
            continue
        if not item.get("description"):
            continue
        valid.append({
            "description": item["description"],
            "source_type": item.get("source_type", ""),
            "why_falsifies": item.get("why_falsifies", ""),
        })
    return valid[:max_candidates]


async def filter_falsification_candidates(
    hypothesis_id: str,
    candidates: list[dict],
) -> list[dict]:
    """Stage 2: filter candidates for direct relevance and feasibility.

    Postconditions:
        Returns a list of dicts with: description, relevance, feasibility,
        strength (all floats clamped to [0, 1]).
        Filters out relevance < 0.5 or feasibility < 0.3.
        Returns [] on LLM failure, empty input, or when
        ENABLE_FALSIFICATION_LOOP is disabled (rule 13 stub).
    """
    if not ENABLE_FALSIFICATION_LOOP:
        return []

    if not candidates:
        return []

    hypothesis = await get_hypothesis(hypothesis_id)
    candidates_str = "\n".join(
        f"- {c.get('description', '')} (source: {c.get('source_type', '')})"
        for c in candidates
    )

    from case_engine.prompts import render as render_prompt
    _pid, _pver, prompt = render_prompt(
        "falsification_filter",
        hypothesis_statement=hypothesis.statement,
        candidates=candidates_str,
    )
    raw = await _call_llm_json(
        prompt,
        temperature=0.2,
        max_retries=3,
        role="CRITIC",
        operation="falsification_filter",
        prompt_id=_pid,
        prompt_version=_pver,
        case_id=hypothesis.case_id,
    )
    if not raw or "filtered" not in raw or not isinstance(raw["filtered"], list):
        return []

    filtered: list[dict] = []
    for item in raw["filtered"]:
        if not isinstance(item, dict) or not item.get("description"):
            continue
        try:
            relevance = float(item.get("relevance", 0))
            feasibility = float(item.get("feasibility", 0))
            strength = float(item.get("strength", 0))
        except (TypeError, ValueError):
            continue
        if relevance < 0.5 or feasibility < 0.3:
            continue
        filtered.append({
            "description": item["description"],
            "relevance": max(0.0, min(1.0, relevance)),
            "feasibility": max(0.0, min(1.0, feasibility)),
            "strength": max(0.0, min(1.0, strength)),
        })
    return filtered


async def create_falsification_action(
    hypothesis_id: str,
    falsification_target: str,
) -> Action:
    """Create a FIND_COUNTEREVIDENCE action for the given target.

    Not LLM-backed and therefore not flag-gated: this is the manual entry
    point an operator (or a scripted benchmark) can use directly. The LLM
    loop that feeds it (run_falsification_loop) IS gated.

    Side effects: INSERT INTO actions (1) + action_proposed event
    (via create_action).
    """
    hypothesis = await get_hypothesis(hypothesis_id)
    return await create_action(
        case_id=hypothesis.case_id,
        action_type="FIND_COUNTEREVIDENCE",
        target=falsification_target,
        expected_outcome=f"Find evidence that would weaken: {hypothesis.statement[:100]}",
        cost_estimate=0.4,
        risk=0.0,
        expected_information_gain=0.6,
        decision_impact=0.7,
        falsification_value=1.0,
        approval_required=False,
        approval_class="READ_ONLY",
    )


async def run_falsification_loop(hypothesis_id: str) -> list[Action]:
    """Run the full two-stage loop. Returns FIND_COUNTEREVIDENCE actions.

    Returns [] when either stage comes back empty or when
    ENABLE_FALSIFICATION_LOOP is disabled (rule 13 stub).

    Side effects: one create_falsification_action (INSERT + event) per
    surviving factor.
    """
    if not ENABLE_FALSIFICATION_LOOP:
        return []

    candidates = await generate_falsification_candidates(hypothesis_id, max_candidates=5)
    if not candidates:
        return []
    filtered = await filter_falsification_candidates(hypothesis_id, candidates)
    if not filtered:
        return []
    actions: list[Action] = []
    for factor in filtered:
        action = await create_falsification_action(
            hypothesis_id=hypothesis_id,
            falsification_target=factor["description"],
        )
        actions.append(action)
    return actions


async def check_hypothesis_falsified(
    hypothesis_id: str,
    margin: float = 0.5,
) -> tuple[bool, Optional[str]]:
    """Check whether contradicting evidence outweighs supporting evidence.

    Postconditions:
        Returns (falsified, reason).
        falsified=True iff contra_score > support_score + margin, where both
        scores are sums of _evidence_weight over the supporting /
        contradicting evidence sets.
        On falsified=True, hypothesis status is set to FALSIFIED (via
        set_hypothesis_status, which emits hypothesis_status_changed) and the
        reason string is returned; otherwise (False, None).
    """
    current = await get_current_version(hypothesis_id)
    supporting = await get_supporting_evidence(hypothesis_id)
    contradicting = await get_contradicting_evidence(hypothesis_id)

    support_score = 0.0
    for e in supporting:
        support_score += await _evidence_weight(e.evidence_id)
    contra_score = 0.0
    for e in contradicting:
        contra_score += await _evidence_weight(e.evidence_id)

    if contra_score > support_score + margin:
        reason = (
            f"Contradicting evidence ({contra_score:.2f}) exceeds "
            f"supporting ({support_score:.2f}) by more than {margin:.2f}"
        )
        await set_hypothesis_status(hypothesis_id, "FALSIFIED", reason)
        return True, reason
    return False, None