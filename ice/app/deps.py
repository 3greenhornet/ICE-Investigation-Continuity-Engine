"""app/deps.py — service shims and FastAPI dependency providers.

Owner: ICE revamp, spec Part 4 §13.4.
Purpose: a thin registry that binds every case_engine/ function the API needs
    onto nine service classes, plus one async-free provider function per
    class for ``Depends(...)``. Route handlers never import case_engine
    directly (except the spec-mandated specialists: _create_source in
    app/routes/evidence.py, run_investigation_cycle in
    app/routes/investigation.py, and the read-only SQL helpers in
    app/routes/decisions.py / replay / trace / health).

Additions beyond the spec text (worklog ice-0):
    * ContradictionService.get — backs GET /contradictions/{id}
      (audit gap #43, public get_contradiction).
    * ActionService.edit — backs PATCH /actions/{action_id}
      (audit gap #10, edit_action).

Part 2 (fix-2-b):
    * EvidenceService.ingest_external — backs POST
      /cases/{case_id}/evidence/from-llm (doc §2.4, ingest_external_analysis).
    * CaseService.create/list pass case_type and the Optional
      required_confidence straight through to create_case/list_cases
      (static-method shims — no signature change needed; fix-2-a extended
      the engine functions, and kwargs flow through unchanged).
    * The /cases/{id}/suggestions, /threads/debt and proposed-edge review
      routes use direct case_engine imports in
      app/routes/investigation.py, matching that file's established
      pattern (see its module docstring).
"""

from case_engine.case import (
    create_case, get_case, list_cases, update_case_status, get_case_summary,
)
from case_engine.evidence import (
    ingest_evidence, ingest_evidence_from_file, ingest_evidence_from_url,
    ingest_external_analysis, mark_superseded, update_evidence_status,
    get_evidence, get_active_evidence, get_stale_or_superseded,
)
from case_engine.claim import (
    extract_claims, create_claim, link_evidence_to_claim,
    get_claim, get_claims_for_case, get_claims_for_hypothesis,
)
from case_engine.hypothesis import (
    create_hypothesis, get_hypothesis, get_current_version,
    get_version_history, recompute_hypothesis_confidence,
    set_hypothesis_status, list_hypotheses_for_case,
)
from case_engine.contradiction import (
    detect_contradiction, get_contradiction, get_active_contradictions,
    find_contradictions_for_claim, resolve_contradiction,
)
from case_engine.information_gap import (
    create_gap, get_gap, list_gaps_for_case, compute_gap_priority, resolve_gap,
)
from case_engine.evidence_graph import get_hypothesis_graph, get_evidence_chain
from case_engine.provenance import compute_evidence_trace_coverage
from case_engine.action import (
    generate_actions, get_action, get_pending_actions,
    approve_action, execute_action, reject_action, edit_action,
)
from case_engine.next_best_action import select_next_best_action
from case_engine.decision import decide
from case_engine.replay import (
    replay_case, replay_without_evidence,
    compute_evidence_causal_influence, compute_decision_attribution,
)


class CaseService:
    create = staticmethod(create_case)
    get = staticmethod(get_case)
    list = staticmethod(list_cases)
    update_status = staticmethod(update_case_status)
    summary = staticmethod(get_case_summary)


class EvidenceService:
    ingest = staticmethod(ingest_evidence)
    ingest_file = staticmethod(ingest_evidence_from_file)
    ingest_url = staticmethod(ingest_evidence_from_url)
    ingest_external = staticmethod(ingest_external_analysis)  # Part 2 §2.4
    supersede = staticmethod(mark_superseded)
    update_status = staticmethod(update_evidence_status)
    get = staticmethod(get_evidence)
    get_active = staticmethod(get_active_evidence)
    get_stale = staticmethod(get_stale_or_superseded)
    get_chain = staticmethod(get_evidence_chain)


class ClaimService:
    extract = staticmethod(extract_claims)
    create = staticmethod(create_claim)
    link = staticmethod(link_evidence_to_claim)
    get = staticmethod(get_claim)
    list_for_case = staticmethod(get_claims_for_case)
    list_for_hypothesis = staticmethod(get_claims_for_hypothesis)


class HypothesisService:
    create = staticmethod(create_hypothesis)
    get = staticmethod(get_hypothesis)
    get_current = staticmethod(get_current_version)
    get_versions = staticmethod(get_version_history)
    recompute = staticmethod(recompute_hypothesis_confidence)
    set_status = staticmethod(set_hypothesis_status)
    list_for_case = staticmethod(list_hypotheses_for_case)
    get_graph = staticmethod(get_hypothesis_graph)


class ContradictionService:
    detect = staticmethod(detect_contradiction)
    get = staticmethod(get_contradiction)  # audit gap #43
    get_active = staticmethod(get_active_contradictions)
    find_for_claim = staticmethod(find_contradictions_for_claim)
    resolve = staticmethod(resolve_contradiction)


class GapService:
    create = staticmethod(create_gap)
    get = staticmethod(get_gap)
    list_for_case = staticmethod(list_gaps_for_case)
    compute_priority = staticmethod(compute_gap_priority)
    resolve = staticmethod(resolve_gap)


class ActionService:
    generate = staticmethod(generate_actions)
    get = staticmethod(get_action)
    get_pending = staticmethod(get_pending_actions)
    approve = staticmethod(approve_action)
    execute = staticmethod(execute_action)
    reject = staticmethod(reject_action)
    edit = staticmethod(edit_action)  # audit gap #10


class DecisionService:
    decide = staticmethod(decide)
    attribution = staticmethod(compute_decision_attribution)


class ReplayService:
    replay = staticmethod(replay_case)
    counterfactual = staticmethod(compute_evidence_causal_influence)
    replay_without = staticmethod(replay_without_evidence)


def case_service() -> CaseService:
    return CaseService()


def evidence_service() -> EvidenceService:
    return EvidenceService()


def claim_service() -> ClaimService:
    return ClaimService()


def hypothesis_service() -> HypothesisService:
    return HypothesisService()


def contradiction_service() -> ContradictionService:
    return ContradictionService()


def gap_service() -> GapService:
    return GapService()


def action_service() -> ActionService:
    return ActionService()


def decision_service() -> DecisionService:
    return DecisionService()


def replay_service() -> ReplayService:
    return ReplayService()