"""case_engine/induction.py — the Hypothesis Induction Engine (P2).

Owner:      Hari ICE "addition and improvements" pass. The single most
            consequential gap from both audit passes: "the ordinary
            investigation cycle does not generate new hypotheses. It
            recomputes existing ones" / "where is the mechanism that
            generates those three explanations in the first place?"
Purpose:    Independently generate multiple, genuinely DIFFERENT candidate
            explanations from the raw evidence of a case — each one a
            fully structured candidate, not a sentence:

              Field                 What it answers
              ------------------    ----------------------------------------
              mechanism             what actually caused the outcome
              supporting            which claims support it
              contradicting         which claims weaken it
              predictions           what else should be observable if true
              discriminating_test   what separates it from competitors
              falsification         what result would reject/downgrade it
              assumptions           what is assumed but not established
              provenance            who proposed it, from which evidence
              descriptors           causal level / mechanism class / time
                                    scale — the coordinates of the
                                    quality-diversity archive

Pipeline (the Co-Scientist / Robin generate → critique → tournament loop,
realized as a DETERMINISTIC ORCHESTRATION of typed steps — not autonomous
agents; the Robin lesson: "a deterministic pipeline with intelligent
individual steps can beat an elaborate agentic architecture"):

    1. GENERATE   — LLM path (hypothesis_induction prompt, GENERATOR role)
                    when a provider is configured; otherwise a
                    DETERMINISTIC fault-class induction path that analyzes
                    the extracted claims for signal patterns (latency
                    regression after deploy, memory growth, dependency
                    timeouts, auth failures, data drift, capacity load) and
                    instantiates explanation templates. The offline path is
                    what makes the no-leakage benchmark runnable without
                    API keys — it is a real mechanism, not a stub.
    2. DIVERSIFY  — quality-diversity archive (MAP-Elites lite): candidates
                    are binned on (causal_level × mechanism_class) and only
                    the best-scoring candidate per cell survives, so a
                    conventional leader cannot flood out novel directions
                    (the pyribs-inspired inversion).
    3. CRITIQUE   — LLM CRITIC role attacks each surviving candidate
                    (unsupported assumptions, unexplained claims); offline
                    path does the same deterministically against the claim
                    set. Critiques are recorded on the candidate.
    4. TOURNAMENT — round-robin pairwise comparison (RANKER role / offline
                    deterministic coverage comparison) ordering candidates.
    5. PERSIST    — dedupe against existing hypotheses, create new
                    hypotheses (origin='ice_generated', initial confidence
                    deliberately low: 0.3 — new explanations earn belief
                    through evidence, not through generation), register
                    their predictions, emit induction_completed.

Also exposes: unexplained_observations(case_id) — claims no surviving
candidate accounts for — the input for endogenous curiosity.
Deps:       db.connection, case_engine (events), hypothesis, claim,
            predictions, llm, prompts. Read path imports evidence lazily.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import DatabaseUnavailableError
from case_engine.hypothesis import list_hypotheses_for_case, create_hypothesis
from case_engine.claim import get_claims_for_case
from case_engine.prompts import render as render_prompt
from case_engine import llm as llm_mod

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The candidate model
# ---------------------------------------------------------------------------


@dataclass
class HypothesisCandidate:
    """One fully structured explanation candidate (audit table, first pass)."""

    statement: str
    mechanism: str
    supporting_observations: list[str] = field(default_factory=list)
    contradicting_observations: list[str] = field(default_factory=list)
    predictions: list[dict] = field(default_factory=list)
    discriminating_test: str = ""
    falsification_condition: str = ""
    assumptions: list[str] = field(default_factory=list)
    origin: str = "ice_induction"
    origin_detail: Optional[str] = None
    descriptors: dict = field(default_factory=dict)
    critique: dict = field(default_factory=dict)
    score: float = 0.0
    score_factors: dict = field(default_factory=dict)

    # --- scoring ----------------------------------------------------------

    def compute_score(self) -> float:
        """Coverage − conflict + testability, with the factor breakdown.

        Rule 11 (silent scoring is forbidden): every component is recorded
        in score_factors.
        """
        support = len(self.supporting_observations)
        conflict = len(self.contradicting_observations)
        total = support + conflict
        coverage = (support / total) if total else 0.5
        # testability: has predictions + a discriminating test + a
        # falsification condition?
        testability = (
            (0.4 if self.predictions else 0.0)
            + (0.3 if self.discriminating_test else 0.0)
            + (0.3 if self.falsification_condition else 0.0)
        )
        support_bonus = min(1.0, support / 5.0)
        raw = 0.5 * coverage + 0.3 * support_bonus + 0.2 * testability
        self.score_factors = {
            "coverage": round(coverage, 3),
            "support_count": support,
            "conflict_count": conflict,
            "support_bonus": round(support_bonus, 3),
            "testability": round(testability, 3),
        }
        self.score = round(max(0.0, min(1.0, raw)), 4)
        return self.score


# ---------------------------------------------------------------------------
# Deterministic signal analysis (the offline GENERATE step)
# ---------------------------------------------------------------------------

_FAULT_TEMPLATES: list[dict] = [
    {
        "key": "deployment_regression",
        "signals": ("deploy", "deployment", "release", "rollout", "v42", "version"),
        "causal_level": "system",
        "mechanism_class": "recent_change",
        "time_scale": "immediate",
        "statement": "A recent deployment introduced a regression that caused the observed problem",
        "mechanism": "The change shipped in the most recent deployment altered the behavior of the affected component (config default, code path, or dependency version), producing the observed anomaly",
        "predictions": [
            "The problem's onset time coincides with the deployment window",
            "Reverting the deployment removes the problem",
            "Components untouched by the deployment remain healthy",
        ],
        "discriminating_test": "Compare system behavior immediately before and after the deployment timestamp",
        "falsification_condition": "The anomaly predates the deployment, or affected components were not touched by it",
        "assumptions": ["The deployment actually reached the affected environment", "No other change happened in the same window"],
    },
    {
        "key": "resource_exhaustion",
        "signals": ("latency", "timeout", "pool", "connection", "queue", "saturation", "slow", "p95", "memory", "leak", "oom"),
        "causal_level": "component",
        "mechanism_class": "resource_exhaustion",
        "time_scale": "cumulative",
        "statement": "A resource (connections, memory, threads, queue capacity) is being exhausted until the system degrades",
        "mechanism": "Demand or a leak gradually consumes a bounded resource; past the limit, requests wait, fail or are killed",
        "predictions": [
            "Utilization of the bounded resource approaches its configured limit around the failure onset",
            "Wait/queue times rise before errors appear",
            "Restarting or raising the limit temporarily restores service",
        ],
        "discriminating_test": "Inspect the specific resource's utilization and wait metrics across the failure window",
        "falsification_condition": "The resource stays well below its limit while failures occur",
        "assumptions": ["The resource has a fixed bound", "Metrics sample the true utilization"],
    },
    {
        "key": "dependency_degradation",
        "signals": ("dependency", "downstream", "upstream", "third-party", "external", "database", "api", "network", "dns"),
        "causal_level": "environment",
        "mechanism_class": "dependency_failure",
        "time_scale": "immediate",
        "statement": "An external or downstream dependency degraded and propagated the failure",
        "mechanism": "A system the affected service depends on became slow or unavailable; without isolation, the failure propagates upstream",
        "predictions": [
            "Dependency latency/error metrics rise before the affected service's own metrics",
            "Every consumer of that dependency suffers, not just one service",
            "The dependency's own health signals show the anomaly directly",
        ],
        "discriminating_test": "Compare dependency-side metrics with consumer-side metrics over the same window",
        "falsification_condition": "The dependency is healthy while the affected service fails",
        "assumptions": ["The dependency is actually used on the failing path", "Its health is observable"],
    },
    {
        "key": "misconfiguration",
        "signals": ("config", "configuration", "setting", "max_connections", "limit", "threshold", "flag", "parameter", "tuning"),
        "causal_level": "system",
        "mechanism_class": "configuration_error",
        "time_scale": "immediate",
        "statement": "A misconfigured value (limit, threshold, flag) produces the observed behavior",
        "mechanism": "An operator- or deploy-provided configuration value is wrong for the current workload, silently capping or misrouting behavior",
        "predictions": [
            "The effective configuration on the affected environment differs from the intended/baseline value",
            "Correcting the value removes the anomaly without code changes",
            "Environments with the correct value do not show the problem",
        ],
        "discriminating_test": "Dump and diff the live effective configuration against the known-good baseline",
        "falsification_condition": "The effective configuration matches the baseline everywhere",
        "assumptions": ["A baseline value is known", "Effective (not intended) config is inspectable"],
    },
    {
        "key": "capacity_load",
        "signals": ("traffic", "load", "throughput", "qps", "spike", "growth", "users", "requests"),
        "causal_level": "environment",
        "mechanism_class": "capacity_limit",
        "time_scale": "cumulative",
        "statement": "A load increase exceeded the system's provisioned capacity",
        "mechanism": "Organic or event-driven traffic growth crossed the capacity envelope; the system degrades under load it was sized below",
        "predictions": [
            "Request volume rises before the anomaly onset",
            "Degradation scales with offered load",
            "Shedding load or adding capacity restores latency",
        ],
        "discriminating_test": "Plot offered load against capacity and latency over the incident window",
        "falsification_condition": "Load is flat or declining across the failure window",
        "assumptions": ["Load is measurable", "No simultaneous capacity reduction occurred"],
    },
    {
        "key": "credential_expiry",
        "signals": ("auth", "authentication", "certificate", "cert", "token", "credential", "permission", "401", "403", "expired"),
        "causal_level": "component",
        "mechanism_class": "credential_or_access",
        "time_scale": "delayed",
        "statement": "A credential, certificate or permission expired or rotated incorrectly",
        "mechanism": "An expiring/rotated credential was not renewed or was renewed inconsistently across replicas, failing authentication on some paths",
        "predictions": [
            "Failures begin at the credential's expiry timestamp",
            "Errors are authentication-specific (401/403/handshake), not generic timeouts",
            "Replicas holding the renewed credential behave correctly while others fail",
        ],
        "discriminating_test": "Check the credential's validity window and compare replicas' held versions",
        "falsification_condition": "All credentials are valid and identical across replicas during the failure window",
        "assumptions": ["Credential validity is inspectable", "Failures are auth-related"],
    },
    {
        "key": "data_inconsistency",
        "signals": ("inconsistent", "mismatch", "drift", "stale", "replica", "replication", "cache", "diverge", "duplicate"),
        "causal_level": "system",
        "mechanism_class": "data_consistency",
        "time_scale": "delayed",
        "statement": "Divergent or stale data between replicas/caches produces the observed inconsistency",
        "mechanism": "A replica, cache or async pipeline lags or diverges from the source of truth; reads hit the stale copy",
        "predictions": [
            "Reading from the primary/source returns different values than the replica/cache",
            "The divergence window matches the replication/eviction lag",
            "Forcing a cache flush or replica resync eliminates the mismatch",
        ],
        "discriminating_test": "Compare the same logical read against primary and replica/cache simultaneously",
        "falsification_condition": "Primary and replicas agree on every read during the incident",
        "assumptions": ["Reads can be routed to both sources", "The comparison is observable"],
    },
    {
        "key": "observation_artifact",
        "signals": ("metric", "dashboard", "monitor", "alert", "measurement", "sample", "aggregation"),
        "causal_level": "environment",
        "mechanism_class": "measurement_artifact",
        "time_scale": "immediate",
        "statement": "The reported anomaly is (partly) a measurement or aggregation artifact, not a real system fault",
        "mechanism": "Sampling, aggregation window, or a monitoring change makes healthy behavior appear anomalous; the underlying service is fine",
        "predictions": [
            "Independent measurement paths disagree with the anomalous one",
            "The anomaly aligns with a monitoring/config change, not a system change",
            "User-visible behavior remains normal while the metric looks bad",
        ],
        "discriminating_test": "Re-measure through an independent path (different tool, raw logs) for the same window",
        "falsification_condition": "Independent measurement paths confirm the anomaly exactly",
        "assumptions": ["An independent measurement path exists"],
    },
]

_CONTRARIAN_NOTE = (
    "Deliberately retained contrarian candidate: challenges the assumption "
    "that the reported observation is a real system fault (CIA Tradecraft "
    "'Key Assumptions Check' / 'High-Impact Low-Probability' inversion)."
)


_SUFFIXES = ("ations", "ation", "ional", "ions", "ion", "ities", "ity",
             "ness", "ments", "ment", "ings", "ing", "ed", "es", "s")


def _stem(token: str) -> str:
    """Crude deterministic stemmer (connection ~= connections ~= exhausted
    ~= exhaustion). Not linguistic — just enough overlap normalization for
    grounding, dedupe and benchmark matching."""
    t = token
    for suffix in _SUFFIXES:
        if t.endswith(suffix) and len(t) - len(suffix) >= 4:
            return t[: -len(suffix)]
    return t


def _claim_tokens(statement: str) -> set[str]:
    return {
        _stem(t)
        for t in re.findall(r"[a-z0-9]+", (statement or "").lower())
        if len(t) > 2
    }


def _deterministic_candidates(claims: list[dict], objective: str) -> list[HypothesisCandidate]:
    """Instantiate fault-class templates grounded in the observed claims.

    A template fires when ≥1 claim (or the objective) contains one of its
    signals. Its supporting/contradicting observations are the claims whose
    token overlap with the template's signal set is non-empty (support) or
    which contain contrast cues while overlapping (contradict).
    """
    corpus = " ".join(
        [objective] + [c["statement"] for c in claims]
    ).lower()
    candidates: list[HypothesisCandidate] = []

    for tpl in _FAULT_TEMPLATES:
        matched_signals = [s for s in tpl["signals"] if s in corpus]
        if not matched_signals:
            continue
        supporting: list[str] = []
        contradicting: list[str] = []
        signal_tokens = set(tpl["signals"])
        for c in claims:
            toks = _claim_tokens(c["statement"])
            if toks & signal_tokens:
                if any(neg in c["statement"].lower() for neg in
                       ("unchanged", "not affected", "healthy", "normal",
                        "no change", "ruled out", "disproves")):
                    contradicting.append(c["statement"][:160])
                else:
                    supporting.append(c["statement"][:160])
        # Ground the template in THIS case's observed signals and the
        # objective's salient terms — a generic "a resource is being
        # exhausted" explains nothing; "… (observed signals: connection
        # pool queue latency; case focus: checkout service p95)" is an
        # explanation an investigator can act on. This grounding is what
        # the audit meant by "concrete names, dates, figures".
        objective_terms = sorted(_claim_tokens(objective))[:4]
        grounding = (
            f" (observed signals: {', '.join(matched_signals[:5])}"
            + (f"; case focus: {' '.join(objective_terms)}" if objective_terms else "")
            + ")"
        )
        cand = HypothesisCandidate(
            statement=tpl["statement"] + grounding,
            mechanism=tpl["mechanism"],
            supporting_observations=supporting,
            contradicting_observations=contradicting,
            predictions=[
                {"statement": p, "resolution_condition": p,
                 "probability": 0.6}
                for p in tpl["predictions"]
            ],
            discriminating_test=tpl["discriminating_test"],
            falsification_condition=tpl["falsification_condition"],
            assumptions=list(tpl["assumptions"]),
            origin="ice_induction_deterministic",
            origin_detail=(
                f"fault-class template '{tpl['key']}' fired on signals: "
                + ", ".join(matched_signals[:5])
            ),
            descriptors={
                "causal_level": tpl["causal_level"],
                "mechanism_class": tpl["mechanism_class"],
                "time_scale": tpl["time_scale"],
            },
        )
        cand.compute_score()
        candidates.append(cand)

    return candidates


def _deterministic_critique(
    candidate: HypothesisCandidate, claims: list[dict]
) -> dict:
    """Offline critique: unsupported assumptions & unexplained claims."""
    claim_statements = [c["statement"] for c in claims]
    explained = set(candidate.supporting_observations)
    unexplained = [
        s[:120] for s in claim_statements if s[:160] not in explained
    ]
    return {
        "unsupported_assumptions": [
            a for a in candidate.assumptions
            if not any(
                _claim_tokens(a) & _claim_tokens(s) for s in claim_statements
            )
        ],
        "evidence_conflicts": list(candidate.contradicting_observations[:3]),
        "testability_gaps": [],
        "unexplained_claims": unexplained[:5],
        "improvement": (
            "Sharpen the mechanism to reference the specific entities and "
            "metrics present in the unexplained claims."
            if unexplained else "Grounded in the current claim set."
        ),
    }


# ---------------------------------------------------------------------------
# Quality-diversity archive (MAP-Elites lite)
# ---------------------------------------------------------------------------


def quality_diversity_select(
    candidates: list[HypothesisCandidate],
    per_cell: int = 1,
) -> list[HypothesisCandidate]:
    """Keep the best candidate(s) per (causal_level × mechanism_class) cell.

    The pyribs inversion: preserve strong-but-different candidates instead
    of letting one explanatory direction flood the archive. Candidates
    without descriptors fall into the ('unspecified', 'unspecified') cell.
    """
    cells: dict[tuple[str, str], list[HypothesisCandidate]] = {}
    for cand in candidates:
        key = (
            cand.descriptors.get("causal_level", "unspecified"),
            cand.descriptors.get("mechanism_class", "unspecified"),
        )
        cells.setdefault(key, []).append(cand)
    selected: list[HypothesisCandidate] = []
    for key in sorted(cells):
        cell = sorted(cells[key], key=lambda c: -c.score)
        selected.extend(cell[:per_cell])
    return selected


# ---------------------------------------------------------------------------
# LLM paths
# ---------------------------------------------------------------------------


async def _llm_generate_candidates(
    objective: str, claims: list[dict], n: int, case_id: str
) -> list[HypothesisCandidate]:
    claims_summary = "\n".join(
        f"{i+1}. {c['statement'][:200]}" for i, c in enumerate(claims[:30])
    ) or "(no claims extracted yet)"
    _pid, _pver, prompt = render_prompt(
        "hypothesis_induction", objective=objective,
        claims_summary=claims_summary, n=n,
    )
    raw = await llm_mod._call_llm_json(
        prompt, temperature=0.5, max_retries=2, role="GENERATOR",
        operation="hypothesis_induction", prompt_id=_pid,
        prompt_version=_pver, case_id=case_id,
    )
    if not raw or not isinstance(raw.get("candidates"), list):
        return []
    out: list[HypothesisCandidate] = []
    for item in raw["candidates"]:
        if not isinstance(item, dict) or not item.get("statement"):
            continue
        predictions = []
        for p in item.get("predictions") or []:
            if isinstance(p, str):
                predictions.append(
                    {"statement": p, "resolution_condition": p, "probability": 0.6}
                )
            elif isinstance(p, dict) and p.get("statement"):
                predictions.append(
                    {
                        "statement": p["statement"],
                        "resolution_condition": p.get(
                            "resolution_condition", p["statement"]
                        ),
                        "probability": float(p.get("probability", 0.6)),
                    }
                )
        descriptors = item.get("descriptors") or {}
        cand = HypothesisCandidate(
            statement=str(item["statement"]).strip(),
            mechanism=str(item.get("mechanism", "")).strip(),
            supporting_observations=[
                str(s) for s in (item.get("supporting_observations") or [])[:10]
            ],
            contradicting_observations=[
                str(s) for s in (item.get("contradicting_observations") or [])[:10]
            ],
            predictions=predictions[:3],
            discriminating_test=str(item.get("discriminating_test", "")).strip(),
            falsification_condition=str(
                item.get("falsification_condition", "")
            ).strip(),
            assumptions=[str(a) for a in (item.get("assumptions") or [])[:6]],
            origin="ice_induction_llm",
            origin_detail=f"prompt {_pid}@{_pver}",
            descriptors={
                "causal_level": descriptors.get("causal_level", "unspecified"),
                "mechanism_class": descriptors.get("mechanism_class", "unspecified"),
                "time_scale": descriptors.get("time_scale", "unspecified"),
            },
        )
        cand.compute_score()
        out.append(cand)
    return out


async def _llm_critique(
    candidate: HypothesisCandidate, claims: list[dict], case_id: str
) -> dict:
    claims_summary = "\n".join(
        f"- {c['statement'][:160]}" for c in claims[:20]
    ) or "(no claims)"
    _pid, _pver, prompt = render_prompt(
        "hypothesis_critique",
        statement=candidate.statement,
        mechanism=candidate.mechanism,
        claims_summary=claims_summary,
    )
    raw = await llm_mod._call_llm_json(
        prompt, temperature=0.2, max_retries=2, role="CRITIC",
        operation="hypothesis_critique", prompt_id=_pid,
        prompt_version=_pver, case_id=case_id,
    )
    return raw if isinstance(raw, dict) else {}


async def _llm_tournament(
    candidates: list[HypothesisCandidate], claims: list[dict], case_id: str
) -> list[HypothesisCandidate]:
    """Round-robin pairwise comparison (Robin-style, capped pairs)."""
    if len(candidates) < 2:
        return candidates
    claims_summary = "\n".join(
        f"- {c['statement'][:120]}" for c in claims[:15]
    )
    wins = {id(c): 0 for c in candidates}
    pairs = 0
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            if pairs >= 10:  # cost cap — tournaments beyond 10 pairs add little
                break
            a, b = candidates[i], candidates[j]
            _pid, _pver, prompt = render_prompt(
                "hypothesis_tournament",
                statement_a=a.statement, statement_b=b.statement,
                claims_summary=claims_summary,
            )
            raw = await llm_mod._call_llm_json(
                prompt, temperature=0.1, max_retries=2, role="RANKER",
                operation="hypothesis_tournament", prompt_id=_pid,
                prompt_version=_pver, case_id=case_id,
            )
            pairs += 1
            if isinstance(raw, dict):
                winner = str(raw.get("winner", "tie")).upper()
                if winner == "A":
                    wins[id(a)] += 1
                elif winner == "B":
                    wins[id(b)] += 1
        if pairs >= 10:
            break
    return sorted(candidates, key=lambda c: -wins.get(id(c), 0))


def _deterministic_tournament(candidates: list[HypothesisCandidate]) -> list:
    """Offline ordering: score, then coverage, then testability (stable)."""
    return sorted(
        candidates,
        key=lambda c: (-c.score, -len(c.supporting_observations),
                       -len(c.predictions)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def unexplained_observations(case_id: str) -> list[dict]:
    """Claims not covered by any active hypothesis.

    The curiosity input: material the current explanatory set cannot yet
    account for. Coverage is token-overlap between claim statement and
    hypothesis statements (deterministic; the embedding version is the
    documented upgrade path).
    """
    claims = await get_claims_for_case(case_id)
    hypotheses = await list_hypotheses_for_case(case_id)
    hyp_tokens: list[set[str]] = [
        _claim_tokens(h.statement) for h in hypotheses if h.status not in
        ("FALSIFIED",)
    ]
    unexplained = []
    for c in claims:
        if c.status != "ACTIVE":
            continue
        toks = _claim_tokens(c.statement)
        covered = any(toks & ht for ht in hyp_tokens) if hyp_tokens else False
        if not covered:
            unexplained.append(
                {"claim_id": c.claim_id, "statement": c.statement}
            )
    return unexplained


def _core_statement(statement: str) -> str:
    """The explanation itself, without the '(observed signals: ...)' grounding
    clause — dedupe compares MECHANISMS, not shared case vocabulary."""
    return statement.split(" (observed signals:")[0].strip()


def _overlaps_existing(statement: str, existing_tokens: list[set[str]]) -> bool:
    """Duplicate detection with a MEANINGFUL overlap threshold.

    Version 1 rejected candidates sharing ANY single token. Version 2
    compared the full grounded statements — whose shared boilerplate
    ('observed signals', 'case focus', the case's own terms) made distinct
    mechanisms look like duplicates (7 candidates collapsed to 2). Version
    3 compares the CORE mechanism statements with >= 60% smaller-set
    coverage.
    """
    toks = _claim_tokens(_core_statement(statement))
    if not toks:
        return False
    for et in existing_tokens:
        if not et:
            continue
        overlap = len(toks & et) / min(len(toks), len(et))
        if overlap >= 0.6:
            return True
    return False


async def induce_hypotheses(
    case_id: str,
    max_candidates: int = 6,
    create: bool = True,
    min_score: float = 0.15,
) -> dict:
    """Run the full induction pipeline for a case.

    Returns:
        {
          "candidates": [HypothesisCandidate.to_dict()...],  # archived set
          "created_hypothesis_ids": [...],
          "created_prediction_ids": [...],
          "unexplained_observations": [...],
          "generation_path": "llm" | "deterministic" | "hybrid",
          "deduplicated": int,
        }

    Postconditions (create=True):
        One hypotheses row per surviving, non-duplicate candidate
        (origin='ice_generated', initial_confidence=0.3), one
        hypothesis_predictions row per prediction (probability capped at
        0.7 — generated explanations predict, they do not prophesy), and
        one induction_completed event.
    """
    from case_engine.case import get_case

    case = await get_case(case_id)
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot induce hypotheses: no DB pool")

    claims = [
        {"statement": c.statement, "claim_id": c.claim_id, "type": c.claim_type}
        for c in await get_claims_for_case(case_id)
        if c.status == "ACTIVE"
    ]

    # --- 1. GENERATE (hybrid: LLM when available, deterministic always) ---
    candidates: list[HypothesisCandidate] = []
    generation_path = "deterministic"
    if llm_mod.llm_available():
        llm_cands = await _llm_generate_candidates(
            case.objective, claims, max_candidates, case_id
        )
        if llm_cands:
            candidates.extend(llm_cands)
            generation_path = "llm"
    det_cands = _deterministic_candidates(claims, case.objective)
    if det_cands:
        if candidates:
            generation_path = "hybrid"
        candidates.extend(det_cands)

    if not candidates:
        return {
            "candidates": [],
            "created_hypothesis_ids": [],
            "created_prediction_ids": [],
            "unexplained_observations": await unexplained_observations(case_id),
            "generation_path": generation_path,
            "deduplicated": 0,
        }

    # --- 2. DIVERSIFY (quality-diversity archive) -------------------------
    archived = quality_diversity_select(candidates, per_cell=1)
    archived = [c for c in archived if c.score >= min_score]

    # --- 3. CRITIQUE -------------------------------------------------------
    for cand in archived:
        if llm_mod.llm_available():
            cand.critique = await _llm_critique(cand, claims, case_id)
        else:
            cand.critique = _deterministic_critique(cand, claims)

    # --- 4. TOURNAMENT ------------------------------------------------------
    if llm_mod.llm_available():
        ranked = await _llm_tournament(archived, claims, case_id)
    else:
        ranked = _deterministic_tournament(archived)

    # --- 5. PERSIST ---------------------------------------------------------
    existing = await list_hypotheses_for_case(case_id)
    existing_tokens = [_claim_tokens(h.statement) for h in existing]

    created_hypothesis_ids: list[str] = []
    created_prediction_ids: list[str] = []
    deduplicated = 0

    if create:
        from case_engine.predictions import create_prediction
        from case_engine.evidence_graph import add_edge, EdgeRelation

        # claim index for grounding the supporting-observation links
        claim_by_tokens = {
            frozenset(_claim_tokens(c["statement"])): c["claim_id"]
            for c in claims
        }

        for cand in ranked[:max_candidates]:
            if _overlaps_existing(cand.statement, existing_tokens):
                deduplicated += 1
                continue
            try:
                hyp = await create_hypothesis(
                    case_id=case_id,
                    statement=cand.statement,
                    initial_confidence=0.3,
                    reason=f"Induction: {cand.origin_detail or cand.origin}",
                    origin="ice_generated",
                    origin_detail=cand.origin_detail or cand.origin,
                )
            except ValueError:
                # unique-statement collision with a concurrent creator
                deduplicated += 1
                continue
            created_hypothesis_ids.append(hyp.hypothesis_id)
            existing_tokens.append(
                _claim_tokens(_core_statement(cand.statement))
            )

            # -----------------------------------------------------------
            # Ground the candidate: link its supporting observations to
            # the new hypothesis with evidence-graph edges (provenance in
            # metadata: origin=ice_induction, removable/reviewable like
            # every other edge). WITHOUT these links every induced
            # candidate sits at the same ~0.5 confidence and the ranking
            # is arbitrary — "supporting observations" must be edges, not
            # just a text field, for recompute_hypothesis_confidence to
            # differentiate the explanations.
            # -----------------------------------------------------------
            linked_claims = 0
            for obs in cand.supporting_observations[:6]:
                obs_tokens = set(_claim_tokens(obs))
                best_claim_id, best_overlap = None, 0.0
                for ctokens, claim_id in claim_by_tokens.items():
                    if not ctokens or not obs_tokens:
                        continue
                    overlap = len(obs_tokens & ctokens) / min(
                        len(obs_tokens), len(ctokens)
                    )
                    if overlap > best_overlap:
                        best_claim_id, best_overlap = claim_id, overlap
                if best_claim_id is not None and best_overlap >= 0.5:
                    try:
                        await add_edge(
                            case_id=case_id,
                            from_node=best_claim_id,
                            from_type="claim",
                            to_node=hyp.hypothesis_id,
                            to_type="hypothesis",
                            relation=EdgeRelation.SUPPORTS,
                            weight=round(best_overlap, 3),
                            metadata={
                                "origin": "ice_induction",
                                "candidate_origin": cand.origin,
                                "overlap": round(best_overlap, 3),
                            },
                        )
                        linked_claims += 1
                    except Exception as exc:
                        logger.debug(
                            "induction support edge failed: %s", exc
                        )
            for pred in cand.predictions[:3]:
                try:
                    rec = await create_prediction(
                        case_id=case_id,
                        hypothesis_id=hyp.hypothesis_id,
                        statement=pred["statement"],
                        resolution_condition=pred["resolution_condition"],
                        probability=min(0.7, max(0.1, pred.get("probability", 0.6))),
                    )
                    created_prediction_ids.append(rec["prediction_id"])
                except Exception as exc:
                    logger.debug("prediction creation failed: %s", exc)

        if created_hypothesis_ids:
            await _emit_event(
                case_id,
                "induction_completed",
                {
                    "case_id": case_id,
                    "candidates_generated": len(candidates),
                    "candidates_archived": len(archived),
                    "hypotheses_created": created_hypothesis_ids,
                    "predictions_created": created_prediction_ids,
                    "generation_path": generation_path,
                },
            )

    return {
        "candidates": [
            {
                "statement": c.statement,
                "mechanism": c.mechanism,
                "descriptors": c.descriptors,
                "score": c.score,
                "score_factors": c.score_factors,
                "supporting_observations": c.supporting_observations,
                "contradicting_observations": c.contradicting_observations,
                "predictions": [p["statement"] for p in c.predictions],
                "discriminating_test": c.discriminating_test,
                "falsification_condition": c.falsification_condition,
                "assumptions": c.assumptions,
                "critique": c.critique,
                "origin": c.origin,
            }
            for c in ranked
        ],
        "created_hypothesis_ids": created_hypothesis_ids,
        "created_prediction_ids": created_prediction_ids,
        "unexplained_observations": await unexplained_observations(case_id),
        "generation_path": generation_path,
        "deduplicated": deduplicated,
    }
