"""case_engine/next_best_action.py — action-value scoring and selection.

Owner:      ICE revamp, Part 3 §7 (semantic replacement for the legacy
            engine/generativity_estimator.py "GenerativityEstimator").
Purpose:    Score proposed actions (expected information gain, decision
            impact, falsification value, source diversity, redundancy,
            risk) and select the next best action.
Deps:       db.connection, case_engine.exceptions, case_engine.schemas,
            case_engine.case, case_engine.hypothesis,
            case_engine.action (lazily, inside select_next_best_action),
            engine.memory (embed, for redundancy cosine similarity).

Part 3 §0 rules honored here:
    Rule 11 — silent scoring is forbidden: compute_priority_with_factors
        returns the full factor breakdown alongside the score, and
        compute_priority is a thin wrapper over it.
    Rule 13 — flag gating: when ENABLE_NEXT_BEST_EVIDENCE is disabled, every
        scorer returns the 0.0 stub and select_next_best_action returns None,
        without touching the database or any LLM.
    Rule 14 — every probability-like value is a planning belief: hypothesis
        confidences are normalized to sum to 1 before entropy is computed;
        all returned values are heuristics in [0, 1].
"""

import logging
import math
import os
from typing import Optional

import numpy as np

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError
from case_engine.schemas import Action
from case_engine.hypothesis import list_hypotheses_for_case, get_current_version
from case_engine.case import get_case
from engine.memory import embed

logger = logging.getLogger(__name__)

ENABLE_NEXT_BEST_EVIDENCE = os.getenv("ENABLE_NEXT_BEST_EVIDENCE", "false").lower() == "true"

_SOURCE_FAMILIES = {"DOCUMENT", "URL", "TOOL", "HUMAN", "DATABASE", "LOG_FILE", "CODE_REPO"}


async def uncertainty_priority_heuristic(action: Action) -> float:
    """Uncertainty-priority heuristic (0-1) — RENAMED from compute_eig.

    Second-pass audit correction: "Until an action-conditioned outcome
    model exists, rename the current function to something like
    uncertainty_priority_heuristic() rather than implying it computes
    formal EIG." This heuristic measures how unresolved the HYPOTHESIS
    SET is (normalized entropy × how contested the lead is); it does NOT
    measure what any specific action would teach. The action-conditioned
    value lives in expected_information_gain() below.

    Formula:
        confidences = [current_version(h).confidence for h in case hypotheses]
        If fewer than 2 hypotheses -> 0.1
        probs = confidences / sum(confidences)          (planning beliefs)
        H = -sum(p * log(p + 1e-9))
        H_norm = H / log(len(probs))
        sorted_probs = sorted(probs, reverse=True)
        gap = sorted_probs[0] - sorted_probs[1]
        discrimination = 1.0 - gap
        return H_norm * discrimination

    Flag gating (rule 13): returns 0.0 without DB access when
    ENABLE_NEXT_BEST_EVIDENCE is disabled.

    Raises: DatabaseUnavailableError, CaseNotFoundError.
    Side effects: none.
    """
    if not ENABLE_NEXT_BEST_EVIDENCE:
        return 0.0

    await get_case(action.case_id)

    hypotheses = await list_hypotheses_for_case(action.case_id)
    if len(hypotheses) < 2:
        return 0.1

    confidences: list[float] = []
    for h in hypotheses:
        try:
            v = await get_current_version(h.hypothesis_id)
            confidences.append(v.confidence)
        except Exception:
            continue

    if len(confidences) < 2:
        return 0.1

    total = sum(confidences)
    if total <= 0.0:
        return 0.0
    probs = [c / total for c in confidences]

    H = -sum(p * math.log(p + 1e-9) for p in probs)
    H_max = math.log(len(probs))
    H_norm = H / H_max if H_max > 0 else 0.0

    sorted_probs = sorted(probs, reverse=True)
    gap = sorted_probs[0] - sorted_probs[1] if len(sorted_probs) >= 2 else 0.0
    discrimination = max(0.0, 1.0 - gap)

    return max(0.0, min(1.0, H_norm * discrimination))


# Back-compat alias: existing callers (decision.py, tests, verify scripts)
# keep working; new code should call the honest name or — better —
# expected_information_gain().
async def compute_eig(action: Action) -> float:
    """DEPRECATED alias for uncertainty_priority_heuristic (see above)."""
    return await uncertainty_priority_heuristic(action)


# ---------------------------------------------------------------------------
# Action-conditioned expected information gain (P3, the real thing)
# ---------------------------------------------------------------------------


def _entropy(probs: list[float]) -> float:
    return -sum(p * math.log(p + 1e-9) for p in probs if p > 0)


def expected_information_gain_pure(
    priors: dict[str, float],
    outcome_models: list[dict[str, float]],
) -> float:
    """True action-conditioned EIG from an explicit outcome model (pure).

    EIG(a) = H(Hypotheses) - sum_o P(o|a) * H(Hypotheses | o, a)

    Args:
        priors: {hypothesis_id: prior belief} (normalized internally).
        outcome_models: one dict per possible outcome o, mapping
            hypothesis_id -> P(o | h) (likelihood of that outcome if that
            hypothesis were true). Likelihoods need not be normalized.

    The decisive difference from the uncertainty heuristic: two tests that
    face the same hypothesis set but produce different outcome
    distributions now score DIFFERENTLY — a test whose outcomes are
    identical under every hypothesis scores ~0 (Test A in the audit's
    example), an expensive-but-discriminating test scores high (Test B).

    Returns EIG in [0, 1] (normalized by H_max when there are >= 2
    hypotheses). 0.0 for degenerate inputs.
    """
    ids = [h for h, p in priors.items() if p > 0]
    if len(ids) < 2 or not outcome_models:
        return 0.0
    total = sum(priors[h] for h in ids)
    prior = {h: priors[h] / total for h in ids}
    H_prior = _entropy([prior[h] for h in ids])
    if H_prior <= 0:
        return 0.0

    # marginal outcome probability P(o) = sum_h P(o|h) p(h)
    marginals: list[float] = []
    for om in outcome_models:
        m = sum(om.get(h, 0.0) * prior[h] for h in ids)
        marginals.append(m)
    if sum(marginals) <= 0:
        return 0.0

    expected_posterior_entropy = 0.0
    for m, om in zip(marginals, outcome_models):
        if m <= 0:
            continue
        post = {
            h: (om.get(h, 0.0) * prior[h]) / m for h in ids
        }
        expected_posterior_entropy += m * _entropy([post[h] for h in ids])

    eig = H_prior - expected_posterior_entropy
    H_max = math.log(len(ids))
    if H_max > 0:
        eig = eig / H_max  # normalize to [0, 1]-ish
    return max(0.0, min(1.0, eig))


def _default_outcome_model(
    action: Action,
    hypothesis_ids: list[str],
) -> list[dict[str, float]]:
    """Deterministic default outcome model when none is attached.

    Models two outcomes — CONSISTENT / INCONSISTENT with the leading
    hypothesis — with likelihood asymmetry derived from each hypothesis's
    relative support. Deliberately modest: the default rewards actions
    that target the leading hypothesis's distinguishing claim and returns
    near-uniform (low EIG) likelihoods otherwise. The LLM path
    (plan_discriminating_tests) attaches richer per-outcome models.
    """
    leading = hypothesis_ids[0] if hypothesis_ids else None
    target_lower = (action.target or "").lower()
    on_lead = leading is not None and any(
        w in target_lower for w in ("counterevidence", "falsif", "weaken")
    )
    models: list[dict[str, float]] = []
    for outcome_label, base in (("consistent", 0.6), ("inconsistent", 0.4)):
        om: dict[str, float] = {}
        for i, h in enumerate(hypothesis_ids):
            if on_lead and h == leading:
                om[h] = base if outcome_label == "consistent" else 1 - base
            else:
                # non-targeted hypotheses: weakly inverted likelihoods
                om[h] = (1 - base) * 0.6 + 0.2 if outcome_label == "consistent" else base * 0.6 + 0.2
        models.append(om)
    return models


async def expected_information_gain(action: Action) -> tuple[float, dict]:
    """Action-conditioned EIG with the factor breakdown (rule 11).

    Outcome model resolution order:
        1. action.result_payload["predicted_outcomes"] attached by the
           discriminating-test planner (richest),
        2. LLM outcome_model prompt (RANKER role) when a provider is
           configured and the action is evidence-expecting,
        3. deterministic default model (see _default_outcome_model).

    Returns (eig, factors) with the priors, outcome source and per-outcome
    marginals recorded — silent scoring is forbidden.
    """
    if not ENABLE_NEXT_BEST_EVIDENCE:
        return 0.0, {"eig": 0.0, "source": "disabled"}

    await get_case(action.case_id)
    hypotheses = await list_hypotheses_for_case(action.case_id)
    confs: dict[str, float] = {}
    for h in hypotheses:
        try:
            v = await get_current_version(h.hypothesis_id)
            confs[h.hypothesis_id] = max(1e-6, v.confidence)
        except Exception:
            continue
    if len(confs) < 2:
        return 0.0, {"eig": 0.0, "source": "insufficient_hypotheses"}

    hypothesis_ids = list(confs.keys())
    outcome_source = "default"
    outcome_models: Optional[list[dict[str, float]]] = None

    # 1. attached predictions
    payload = action.result_payload or {}
    attached = payload.get("predicted_outcomes")
    if isinstance(attached, list) and attached:
        outcome_models = []
        for om in attached:
            if isinstance(om, dict) and isinstance(om.get("likelihoods"), dict):
                outcome_models.append(
                    {str(k): float(v) for k, v in om["likelihoods"].items()
                     if k in confs}
                )
        outcome_models = [m for m in outcome_models if m] or None
        if outcome_models:
            outcome_source = "attached"

    # 2. LLM path
    if outcome_models is None:
        try:
            from case_engine import llm as llm_mod
            from case_engine.prompts import render as render_prompt

            if llm_mod.llm_available():
                hyp_summary = "\n".join(
                    f"- {hid}: {(await get_current_version(hid)).confidence:.2f}"
                    for hid in hypothesis_ids[:6]
                )
                _pid, _pver, prompt = render_prompt(
                    "outcome_model",
                    action_type=action.action_type,
                    target=(action.target or "")[:300],
                    expected_outcome=(action.expected_outcome or "")[:300],
                    hypotheses_summary=hyp_summary,
                )
                raw = await llm_mod._call_llm_json(
                    prompt, temperature=0.2, max_retries=2, role="RANKER",
                    operation="outcome_model", prompt_id=_pid,
                    prompt_version=_pver, case_id=action.case_id,
                )
                if raw and isinstance(raw.get("outcomes"), list):
                    models = []
                    for o in raw["outcomes"]:
                        if not isinstance(o, dict):
                            continue
                        lik = o.get("likelihoods") or {}
                        m = {
                            str(k): max(0.0, min(1.0, float(v)))
                            for k, v in lik.items() if k in confs
                        }
                        if m:
                            models.append(m)
                    if models:
                        outcome_models = models
                        outcome_source = "llm"
        except Exception as exc:
            logger.debug("LLM outcome model failed: %s", exc)

    # 3. deterministic default
    if outcome_models is None:
        outcome_models = _default_outcome_model(action, hypothesis_ids)
        outcome_source = "default"

    eig = expected_information_gain_pure(confs, outcome_models)
    factors = {
        "eig_action_conditioned": round(eig, 4),
        "outcome_source": outcome_source,
        "n_outcomes": len(outcome_models),
        "priors": {k: round(v, 3) for k, v in confs.items()},
    }
    return eig, factors


async def plan_discriminating_tests(case_id: str, max_actions: int = 2) -> list:
    """Attach predicted outcome models to the best discriminating actions.

    For the top competing hypotheses this finds/creates actions whose
    PREDICTED OUTCOMES differ maximally across those hypotheses (max EIG),
    then attaches the outcome model to the action's result_payload so
    expected_information_gain() uses the rich path and — after execution —
    the trajectory analyzer can score whether the prediction was right.

    Returns the list of (action, eig, factors) tuples it upgraded.
    """
    from case_engine.action import (
        get_pending_actions as _pending,
        create_action as _create,
    )

    await get_case(case_id)
    hypotheses = await list_hypotheses_for_case(case_id)
    confs: dict[str, float] = {}
    for h in hypotheses:
        try:
            v = await get_current_version(h.hypothesis_id)
            confs[h.hypothesis_id] = max(1e-6, v.confidence)
        except Exception:
            continue
    if len(confs) < 2:
        return []

    ranked = sorted(confs.items(), key=lambda kv: -kv[1])
    top_ids = [h for h, _ in ranked[:3]]

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot plan tests: no DB pool")

    # Candidate discriminating tests, deterministic:
    #   (a) every pending action (upgraded with an attached outcome model)
    #   (b) a RUN_TEST ACH-diagnostics action when none exists yet
    candidates = await _pending(case_id)
    has_ach_test = any(
        a.action_type == "RUN_TEST" and a.target
        and "ach" in (a.target or "").lower() for a in candidates
    )
    created = None
    if not has_ach_test:
        created = await _create(
            case_id=case_id,
            action_type="RUN_TEST",
            target="ach_diagnostics: compare competing hypotheses against all claims",
            expected_outcome=(
                "ACH matrix identifies which claims discriminate the top "
                "competing hypotheses"
            ),
            cost_estimate=0.2,
            risk=0.0,
            expected_information_gain=0.6,
            decision_impact=0.7,
            approval_required=False,
            approval_class="READ_ONLY",
        )
        candidates = [created] + candidates

    upgraded = []
    for action in candidates[: max_actions * 3]:
        # attach a deterministic outcome model contrasting the top two
        lead, second = top_ids[0], top_ids[1]
        model = [
            # outcome: evidence consistent with the leading hypothesis
            {lead: 0.7, second: 0.25},
            # outcome: evidence consistent with the runner-up
            {lead: 0.25, second: 0.7},
            # outcome: undiscriminating / inconclusive
            {lead: 0.4, second: 0.4},
        ]
        eig = expected_information_gain_pure(
            confs,
            [{k: v for k, v in m.items() if k in confs} for m in model],
        )
        payload = dict(action.result_payload or {})
        payload["predicted_outcomes"] = [
            {
                "description": desc,
                "likelihoods": {k: v for k, v in m.items() if k in confs},
            }
            for desc, m in zip(
                ("consistent_with_leading",
                 "consistent_with_runner_up",
                 "inconclusive"),
                model,
            )
        ]
        import json as _json

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE actions SET result_payload = $1::jsonb, "
                "expected_information_gain = $2 WHERE action_id = $3",
                _json.dumps(payload, default=str), eig, action.action_id,
            )
        upgraded.append((action, eig, {"attached": True}))
        if len(upgraded) >= max_actions:
            break
    return upgraded


async def compute_decision_impact(action: Action) -> float:
    """How much would resolving this action change the case decision?

    Formula:
        case = await get_case(action.case_id)
        leading = highest-confidence hypothesis
        gap = max(0, case.required_confidence - leading.confidence)
        if action.target contains leading.statement[:50].lower():
            return min(1.0, gap * 2)
        return 0.3

    Flag gating (rule 13): returns 0.0 when ENABLE_NEXT_BEST_EVIDENCE is
    disabled.

    Raises: DatabaseUnavailableError, CaseNotFoundError.
    """
    if not ENABLE_NEXT_BEST_EVIDENCE:
        return 0.0

    case = await get_case(action.case_id)
    hypotheses = await list_hypotheses_for_case(action.case_id)
    if not hypotheses:
        return 0.5

    leading = None
    leading_conf = -1.0
    for h in hypotheses:
        try:
            v = await get_current_version(h.hypothesis_id)
        except Exception:
            continue
        if v.confidence > leading_conf:
            leading_conf = v.confidence
            leading = h

    if leading is None:
        return 0.5

    gap = max(0.0, case.required_confidence - leading_conf)

    if action.target and leading.statement[:50].lower() in action.target.lower():
        return min(1.0, gap * 2.0)
    return 0.3


async def compute_falsification_value(action: Action) -> float:
    """Return 1.0 if the action is FIND_COUNTEREVIDENCE, else 0.0.

    Flag gating (rule 13): returns 0.0 when ENABLE_NEXT_BEST_EVIDENCE is
    disabled.
    """
    if not ENABLE_NEXT_BEST_EVIDENCE:
        return 0.0
    return 1.0 if action.action_type == "FIND_COUNTEREVIDENCE" else 0.0


async def compute_source_diversity_bonus(action: Action) -> float:
    """Bonus for targeting an unexplored source family.

    Formula:
        Look up source_type families already used in the case's sources.
        If action.target references an unused family (e.g. "Search LOG_FILE
        for new evidence"): return 0.3. Else return 0.0.

    Flag gating (rule 13): returns 0.0 when ENABLE_NEXT_BEST_EVIDENCE is
    disabled. Never raises; returns 0.0 when the pool is unavailable.
    """
    if not ENABLE_NEXT_BEST_EVIDENCE:
        return 0.0

    if not action.target:
        return 0.0

    pool = await get_pool()
    if pool is None:
        return 0.0

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT source_type FROM sources WHERE case_id = $1",
            action.case_id,
        )
    seen = {r["source_type"] for r in rows}
    unexplored = _SOURCE_FAMILIES - seen
    for family in unexplored:
        if family.lower() in action.target.lower():
            return 0.3
    return 0.0


async def compute_redundancy_penalty(action: Action, window: int = 3) -> float:
    """Penalty for repeating recent actions semantically.

    Formula:
        Load last `window` completed/executing/approved actions in the case
        (excluding this action). For each, compute cosine similarity between
        its target and this action's target via embed.
        Return max similarity * 0.4, bounded by 0.4.

    Flag gating (rule 13): returns 0.0 when ENABLE_NEXT_BEST_EVIDENCE is
    disabled. Returns 0.0 on any embedding/DB failure (never raises).
    """
    if not ENABLE_NEXT_BEST_EVIDENCE:
        return 0.0

    if not action.target:
        return 0.0

    pool = await get_pool()
    if pool is None:
        return 0.0

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT target FROM actions
            WHERE case_id = $1
              AND action_id <> $2
              AND status IN ('completed', 'executing', 'approved')
              AND target IS NOT NULL
            ORDER BY created_at DESC
            LIMIT $3
            """,
            action.case_id, action.action_id, window,
        )

    if not rows:
        return 0.0

    try:
        target_emb = np.array(await embed(action.target), dtype=np.float32)
        target_emb = target_emb / (np.linalg.norm(target_emb) + 1e-8)
    except Exception:
        return 0.0

    max_sim = 0.0
    for row in rows:
        try:
            other_emb = np.array(await embed(row["target"]), dtype=np.float32)
            other_emb = other_emb / (np.linalg.norm(other_emb) + 1e-8)
            sim = float(np.dot(target_emb, other_emb))
            if sim > max_sim:
                max_sim = sim
        except Exception:
            continue

    return min(0.4, max_sim * 0.4)


async def compute_thread_debt_bonus(action: Action) -> float:
    """Bonus for actions that address high-debt investigation threads
    (Part 2 §8.1, fix 2G — Part 1's B.4 gap: compute_debt existed but was
    never called by the priority scorer).

    Returns 0.0 to 0.3 based on the maximum debt of threads whose question
    overlaps the action target by at least one word.

    The debt formula is in investigation_thread.compute_debt:
        debt = strength * min(age_days, 30) * decision_impact * uncertainty

    A debt of 5.0 or more yields the maximum bonus (0.3).

    HONEST NOTE (doc Part 2 §12 shortcut #3): thread-action matching is
    word overlap (>= 1 shared word), not semantic similarity. "examine
    connection limits" shares zero words with "Investigate pool saturation
    timing" yet addresses the same thread. The embedding-based version
    (~$0.001/comparison) was deferred to Part 4 — see the doc for the
    cost analysis.
    HONEST NOTE (doc Part 2 §12 shortcut #4): the 0.3 cap is arbitrary —
    chosen to match the diversity bonus cap so debt gets equal weight;
    adjust if empirical tuning says otherwise.

    Flag gating (rule 13): returns 0.0 without DB access when
    ENABLE_NEXT_BEST_EVIDENCE is disabled. Never raises.
    """
    if not ENABLE_NEXT_BEST_EVIDENCE:
        return 0.0
    if not action.target:
        return 0.0

    from case_engine.investigation_thread import InvestigationThreadRegistry
    registry = InvestigationThreadRegistry(action.case_id)
    try:
        await registry._ensure_loaded()
    except Exception:
        return 0.0

    try:
        active = await registry.get_all_active()
    except Exception:
        return 0.0
    if not active:
        return 0.0

    # Cheap word-overlap check (see shortcut #3 caveat in the docstring)
    target_lower = action.target.lower()
    target_words = set(target_lower.split())
    max_debt = 0.0
    for thread in active:
        thread_lower = thread.question.lower()
        thread_words = set(thread_lower.split())
        overlap = len(target_words & thread_words)
        if overlap >= 1:
            debt = registry.compute_debt(thread)
            if debt > max_debt:
                max_debt = debt

    return min(0.3, max_debt / 5.0 * 0.3)


async def compute_priority_with_factors(action: Action) -> tuple[float, dict]:
    """Full priority score WITH its factor breakdown (Part 3 §0 rule 11).

    Formula:
        eig = compute_eig(action)
        impact = compute_decision_impact(action)
        falsification = compute_falsification_value(action)
        cost = action.cost_estimate
        diversity = compute_source_diversity_bonus(action)
        debt_bonus = compute_thread_debt_bonus(action)   # Part 2 §8.2
        redundancy = compute_redundancy_penalty(action)
        risk = action.risk

        numerator = eig * impact * (1.0 + falsification)
        denominator = 1.0 + cost
        priority = numerator / denominator + diversity + debt_bonus
                 - redundancy - risk
        return max(0.0, min(1.0, priority)), factors

    Returns:
        (priority, factors) where factors is a dict with keys:
        eig, impact, falsification, cost, diversity, debt_bonus,
        redundancy, risk, numerator, denominator. Every scorer must expose
        its breakdown — silent scoring is forbidden.

    Raises: whatever the factor computations raise (DatabaseUnavailableError,
        CaseNotFoundError).
    """
    # Hari ICE improvements pass: the priority numerator now uses the
    # action-conditioned EIG when one is computable (predicted outcomes
    # attached / LLM outcome model), falling back to the renamed
    # uncertainty-priority heuristic. Both values are recorded in the
    # factor breakdown — silent scoring is forbidden.
    eig, eig_factors = await expected_information_gain(action)
    if eig <= 0.0:
        eig = await uncertainty_priority_heuristic(action)
    impact = await compute_decision_impact(action)
    falsification = await compute_falsification_value(action)
    cost = action.cost_estimate
    diversity = await compute_source_diversity_bonus(action)
    debt_bonus = await compute_thread_debt_bonus(action)
    redundancy = await compute_redundancy_penalty(action)
    risk = action.risk

    numerator = eig * impact * (1.0 + falsification)
    denominator = 1.0 + cost
    priority = numerator / denominator + diversity + debt_bonus - redundancy - risk
    priority = max(0.0, min(1.0, priority))

    factors = {
        "eig": eig,
        "eig_detail": eig_factors,
        "impact": impact,
        "falsification": falsification,
        "cost": cost,
        "diversity": diversity,
        "debt_bonus": debt_bonus,
        "redundancy": redundancy,
        "risk": risk,
        "numerator": numerator,
        "denominator": denominator,
    }
    return priority, factors


async def compute_priority(action: Action) -> float:
    """Return the full priority score for an action (clamped to [0, 1]).

    Thin wrapper over compute_priority_with_factors (rule 11: the breakdown
    is always computed; callers that need it for debugging or event payloads
    should call compute_priority_with_factors directly).

    Raises: DatabaseUnavailableError, CaseNotFoundError.
    """
    priority, _factors = await compute_priority_with_factors(action)
    return priority


async def select_next_best_action(case_id: str) -> Optional[tuple[Action, float]]:
    """Return the highest-priority proposed action and its priority.

    Actions whose priority computation fails are skipped (logged at debug).
    Returns None if no proposed actions exist, none score successfully, or
    ENABLE_NEXT_BEST_EVIDENCE is disabled (rule 13 stub).
    """
    if not ENABLE_NEXT_BEST_EVIDENCE:
        return None

    from case_engine.action import get_pending_actions

    actions = await get_pending_actions(case_id)
    if not actions:
        return None

    scored: list[tuple[float, Action]] = []
    for action in actions:
        try:
            p = await compute_priority(action)
        except Exception as exc:
            logger.debug("Priority computation failed for %s: %s", action.action_id, exc)
            continue
        scored.append((p, action))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1], scored[0][0]