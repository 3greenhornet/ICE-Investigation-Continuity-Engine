"""case_engine/prompts.py — versioned prompt registry (prompt centralisation).

Owner:      Hari ICE "addition and improvements" pass (deferred item
            "Prompt centralisation — consistent, maintainable reasoning
            prompts" from the first-pass audit).
Purpose:    ONE canonical, versioned home for every reasoning prompt used by
            the ICE LLM workflow. Each prompt has a stable ``prompt_id`` and
            a ``version``; every llm.py call records both in the
            ``llm_ledger`` table (migration 020), so any benchmark or
            experiment can answer "which prompt/model change produced this
            result" — the prompt-reproducibility gap from the second-pass
            audit.

Design rules:
    * Prompts are plain format strings (``str.format``) — no f-strings, so
      the template text is inspectable verbatim.
    * Bumping a prompt's version is a deliberate act: copy the old entry to
      the ``superseded`` mapping with its version suffix so old ledger rows
      stay interpretable.
    * ``render`` never fails silently: unknown prompt ids raise KeyError.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Registry: prompt_id -> (version, template)
# ---------------------------------------------------------------------------

_PROMPTS: dict[str, tuple[str, str]] = {
    # --- claim extraction (moved from claim.py, text preserved verbatim) ---
    "claim_extraction": (
        "v1",
        '''You are extracting structured claims from a piece of evidence.

EVIDENCE CONTENT:
"""
{evidence_content}
"""

EVIDENCE TYPE: {evidence_type}
CASE OBJECTIVE: {case_objective}

Extract all distinct claims from this evidence. A claim is a single proposition that could support or contradict a hypothesis.

For each claim, specify:
- statement: the claim in one clear sentence
- claim_type: one of MEASUREMENT, OBSERVATION, CORRELATION, TEMPORAL_ASSOCIATION, CAUSAL_CLAIM, INTERPRETATION, PREDICTION, ASSUMPTION
- scope: a dict of qualifiers like {{"region": "us-east", "service": "checkout"}} or null
- valid_from: ISO 8601 timestamp if the claim is time-bounded, else null
- valid_until: ISO 8601 timestamp if the claim is time-bounded, else null

Rules:
1. Extract ONLY claims directly supported by the evidence text.
2. Do NOT infer, extrapolate, or add background knowledge.
3. Do NOT create claims for opinions, hedges, or questions.
4. If a sentence contains multiple claims, split them.
5. If the evidence contains no extractable claims, return {{"claims": []}}.

Return ONLY a JSON object with this exact shape:
{{"claims": [{{"statement": "...", "claim_type": "...", "scope": {{...}} or null, "valid_from": null, "valid_until": null}}]}}
''',
    ),
    # --- contradiction classification (moved from contradiction.py,
    #     text preserved verbatim) ----------------------------------------
    "contradiction_classification": (
        "v1",
        '''Determine the logical relationship between these two claims.

CLAIM A: {statement_a}
CLAIM B: {statement_b}

Classify the relationship as exactly one of:
- "contradiction": A and B cannot both be true
- "refinement": A is a more specific version of B
- "scope_mismatch": A and B apply to different contexts
- "temporal_mismatch": A and B apply to different time periods
- "independent": A and B are unrelated

Also provide a confidence score from 0.0 to 1.0.

Return ONLY a JSON object:
{{"relation_type": "...", "confidence": 0.0, "reasoning": "..."}}
''',
    ),
    # --- falsification generation (moved from falsification.py) ------------
    "falsification_generation": (
        "v1",
        """You are designing a falsification test for a hypothesis.

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
{{"candidates": [{{"description": "...", "source_type": "...", "why_falsifies": "..."}}]}}""",
    ),
    "falsification_filter": (
        "v1",
        """You are filtering falsification factors for practical relevance.

HYPOTHESIS: {hypothesis_statement}

CANDIDATE FALSIFICATION FACTORS:
{candidates}

For each factor, rate:
- relevance: 0-1, how directly it would test the hypothesis
- feasibility: 0-1, how easily this evidence could be obtained
- strength: 0-1, how much it would weaken the hypothesis if found

Reject factors with relevance < 0.5 or feasibility < 0.3.

Return ONLY a JSON object:
{{"filtered": [{{"description": "...", "relevance": 0.8, "feasibility": 0.6, "strength": 0.7}}]}}""",
    ),
    # --- NEW: hypothesis induction (the P2 reasoning-loop engine) ----------
    "hypothesis_induction": (
        "v1",
        """You are a hypothesis induction engine for an investigation.

CASE OBJECTIVE: {objective}
OBSERVED CLAIMS (extracted from evidence):
{claims_summary}

Generate {n} GENUINELY DIFFERENT candidate explanations for the observed
outcome. Two candidates that share the same causal mechanism are NOT
different — vary the causal level (component / system / environment),
the mechanism class (resource exhaustion, race condition, dependency
failure, configuration error, recent change, capacity limit, external
factor, data corruption, measurement artifact) and the time scale.

For EACH candidate return:
- statement: one-sentence explanation
- mechanism: the specific causal mechanism, concretely
- supporting_observations: list of claim indices (1-based) consistent with it
- contradicting_observations: list of claim indices that weaken it
- predictions: 2-3 observable consequences IF this explanation is true
  that have not yet been observed
- discriminating_test: one observation that would separate it from the
  other candidates
- falsification_condition: what result would make us reject it
- assumptions: what is assumed but not established
- descriptors: {{"causal_level": "component|system|environment",
  "mechanism_class": "...", "time_scale": "immediate|cumulative|delayed"}}

Rules:
- Ground every candidate in the observed claims — no wild speculation.
- Include at least one candidate that challenges an obvious assumption.
- Do NOT include a catch-all "unknown other cause" candidate.

Return ONLY a JSON object:
{{"candidates": [{{...}}]}}""",
    ),
    # --- NEW: candidate critique (generate -> critique -> tournament) ------
    "hypothesis_critique": (
        "v1",
        """You are a critical reviewer of candidate explanations.

CANDIDATE EXPLANATION: {statement}
MECHANISM: {mechanism}
CLAIMS OBSERVED:
{claims_summary}

Identify the weakest points of this candidate:
- unsupported_assumptions: assumptions with no supporting claim
- evidence_conflicts: observed claims this candidate cannot explain
- testability_gaps: why the predictions may not be observable
- improvement: one concrete way to sharpen the explanation

Return ONLY a JSON object:
{{"unsupported_assumptions": ["..."], "evidence_conflicts": ["..."], "testability_gaps": ["..."], "improvement": "..."}}""",
    ),
    # --- NEW: pairwise tournament comparison --------------------------------
    "hypothesis_tournament": (
        "v1",
        """Compare two competing explanations against the evidence.

EXPLANATION A: {statement_a}
EXPLANATION B: {statement_b}
CLAIMS OBSERVED:
{claims_summary}

Which explanation accounts for MORE of the observed claims with FEWER
unsupported assumptions? Consider explanatory coverage, simplicity, and
testability. A tie is a legitimate result.

Return ONLY a JSON object:
{{"winner": "A"|"B"|"tie", "reasoning": "...", "coverage_a": 0.0-1.0, "coverage_b": 0.0-1.0}}""",
    ),
    # --- NEW: outcome model for expected information gain -------------------
    "outcome_model": (
        "v1",
        """You are modeling the possible outcomes of an investigative action.

ACTION: {action_type} — {target}
PURPOSE: {expected_outcome}
COMPETING HYPOTHESES:
{hypotheses_summary}

List the 2-4 DISTINCT observable outcomes this action could produce. For
each outcome, estimate the likelihood under EACH hypothesis (0.0-1.0 —
the probability of seeing that outcome if that hypothesis were true).
Outcomes must be distinguishable from each other by an observer.

Return ONLY a JSON object:
{{"outcomes": [{{"description": "...", "likelihoods": {{"{hypothesis_id_1}": 0.8, "{hypothesis_id_2}": 0.2}}}}]}}""",
    ),
    # --- NEW: causal edge proposal -------------------------------------------
    "causal_edge_proposal": (
        "v1",
        """You are proposing causal relationships for an investigation.

CASE OBJECTIVE: {objective}
TIMELINE OF OBSERVED EVENTS/CLAIMS:
{timeline}

Propose causal edges between observed items. Each edge:
- cause: what produces the effect (event/claim id + label)
- effect: what is produced
- relation: CAUSES (direct), CONTRIBUTES_TO (partial), PRECEDES (temporal
  ordering only — weaker than causation), EXCLUDES (mutually exclusive)
- confidence: 0.0-1.0
- assumptions: what must hold for the edge to be true

Be conservative: PRECEDES is not CAUSES. A temporal order alone never
justifies a CAUSES edge. Mark uncertain edges with low confidence.

Return ONLY a JSON object:
{{"edges": [{{"cause_id": "...", "effect_id": "...", "relation": "CONTRIBUTES_TO", "confidence": 0.5, "assumptions": ["..."]}}]}}""",
    ),
    # --- NEW: contradiction nature classification ---------------------------
    "contradiction_nature": (
        "v1",
        """Classify the NATURE of an apparent contradiction between two claims.

CLAIM A (at {time_a}): {claim_a}
CLAIM B (at {time_b}): {claim_b}
KNOWN TIMELINE:
{timeline}

Choose exactly one nature:
- real_conflict: both cannot be true under any scope/time interpretation
- change_over_time: both true, the world changed between the observations
- scope_difference: both true for different entities/conditions
- missing_intermediate_event: both true, an unobserved event explains the
  transition between them

Return ONLY a JSON object:
{{"nature": "...", "confidence": 0.0-1.0, "explanation": "...", "intermediate_event_hint": "..."}}""",
    ),
    # --- NEW: plan reconciliation -------------------------------------------
    "plan_reconciliation": (
        "v1",
        """You are reconciling a project plan against implementation evidence.

ORIGINAL PLAN STEP: {description} (status: {status})
IMPLEMENTATION EVIDENCE:
{evidence_summary}

Decide:
- achieved: does the evidence show this step's OBJECTIVE is met (even by a
  different implementation than planned)?
- classification: one of
    intentional_evolution (a better/different approach was clearly chosen),
    accidental_divergence (contradicts the plan with no sign of a decision),
    temporary_deviation (acceptable scaffold for the current stage),
    unresolved (not enough evidence to tell)
- revised_step: if the objective is met differently, the step that remains

Return ONLY a JSON object:
{{"achieved": true/false, "classification": "...", "reasoning": "...", "remaining_concern": "..."}}""",
    ),
}

# Superseded prompt versions kept for ledger interpretability.
_SUPERSEDED: dict[str, dict[str, str]] = {}


def get_prompt(prompt_id: str) -> tuple[str, str]:
    """Return (version, template) for a registered prompt id.

    Raises KeyError for unknown ids — silent fallbacks would defeat the
    reproducibility purpose of the registry.
    """
    return _PROMPTS[prompt_id]


def render(prompt_id: str, **fields) -> tuple[str, str, str]:
    """Format a registered prompt.

    Returns (prompt_id, version, rendered_text).
    """
    version, template = _PROMPTS[prompt_id]
    return prompt_id, version, template.format(**fields)


def list_prompts() -> list[dict]:
    """Registry inventory: [{prompt_id, version, chars}]."""
    return [
        {"prompt_id": pid, "version": v, "chars": len(t)}
        for pid, (v, t) in sorted(_PROMPTS.items())
    ]


def register_prompt(
    prompt_id: str,
    version: str,
    template: str,
    supersede: bool = True,
) -> None:
    """Register or bump a prompt at runtime (experiments use this).

    Bumping archives the previous version under
    _SUPERSEDED[prompt_id][old_version].
    """
    if prompt_id in _PROMPTS and supersede:
        old_v, old_t = _PROMPTS[prompt_id]
        _SUPERSEDED.setdefault(prompt_id, {})[old_v] = old_t
    _PROMPTS[prompt_id] = (version, template)


def _template_fingerprint(template: str) -> str:
    """Stable fingerprint of a template (brace-field names sorted)."""
    fields = sorted(set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", template)))
    return ",".join(fields)
