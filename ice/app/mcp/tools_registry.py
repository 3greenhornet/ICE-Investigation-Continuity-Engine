"""tools_registry.py — Comprehensive MCP Tool Registry for ICE.

Exposes ICE's complete cognitive, forensic, goal-evolution, and codebase grounding
capabilities as standardized MCP tools.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ice.mcp.tools")


def _safe(fn: Callable) -> Callable:
    """Wrap a tool function so unhandled exceptions become JSON error payloads."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            logger.exception("Tool %s failed: %s", fn.__name__, exc)
            return {"error": f"{type(exc).__name__}: {str(exc)}"}

    return wrapper


# ======================================================================
# 1. CASE MANAGEMENT
# ======================================================================

@_safe
async def create_case(
    title: str,
    objective: str,
    case_type: str = "debug",
    required_confidence: Optional[float] = None,
    decision_deadline: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new investigation case.

    Args:
        title: Short descriptive case title
        objective: What problem, question, or goal is being investigated
        case_type: 'debug' | 'research' | 'explore'
        required_confidence: 0.0-1.0 (default: 0.85 for debug)
        decision_deadline: Optional ISO-8601 timestamp
    """
    from case_engine.case import create_case as _create
    deadline = None
    if decision_deadline:
        deadline = datetime.fromisoformat(decision_deadline.replace("Z", "+00:00"))
    case = await _create(
        title=title,
        objective=objective,
        case_type=case_type,
        required_confidence=required_confidence,
        decision_deadline=deadline,
    )
    return {"case_id": case.case_id, "status": case.status, "case_type": case.case_type}


@_safe
async def get_case_summary(case_id: str) -> Dict[str, Any]:
    """Get full summary of a case: counts, leading hypothesis, next recommended action."""
    from case_engine.case import get_case_summary as _summary
    return await _summary(case_id)


@_safe
async def list_cases(
    status: Optional[str] = None,
    case_type: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List investigation cases with optional filters."""
    from case_engine.case import list_cases as _list
    cases = await _list(status=status, case_type=case_type, limit=limit)
    return [
        {
            "case_id": c.case_id,
            "title": c.title,
            "status": c.status,
            "case_type": c.case_type,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in cases
    ]


# ======================================================================
# 2. GOALS & EVOLVING PROJECT MODEL (ICE Superpower)
# ======================================================================

@_safe
async def ice_create_goal(
    case_id: str,
    statement: str,
    kind: str = "goal",
    clarity_level: int = 1,
    origin: str = "developer_statement",
    supersedes: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a goal, subgoal, invariant, constraint, or assumption in the Evolving Project Model.

    Clarity levels:
      1: Intuitive / vague intention
      2: Observable success criteria
      3: Bound to codebase components
      4: Formal test / invariant specification
    """
    from case_engine.goals import create_goal as _cg
    return await _cg(
        case_id=case_id,
        statement=statement,
        kind=kind,
        clarity_level=clarity_level,
        origin=origin,
        supersedes=supersedes,
    )


@_safe
async def ice_list_goals(
    case_id: str,
    kind: Optional[str] = None,
    include_superseded: bool = False,
) -> List[Dict[str, Any]]:
    """List goals, invariants, and milestones for an investigation case."""
    from case_engine.goals import list_goals as _lg
    return await _lg(case_id=case_id, kind=kind, include_superseded=include_superseded)


@_safe
async def ice_reconcile_goals(case_id: str) -> Dict[str, Any]:
    """Audit project goals against evidence to discover completed, diverged, or blocked goals."""
    from case_engine.goals import reconcile_goals as _rg
    return await _rg(case_id)


@_safe
async def ice_intent_profile(case_id: str) -> Dict[str, Any]:
    """Get the distribution of 13 intent kinds and clarity levels for a project."""
    from case_engine.goals import intent_profile as _ip
    return await _ip(case_id)


@_safe
async def ice_verify_code_maturity(
    statement: str,
    goal_id: Optional[str] = None,
    kind: str = "goal",
) -> Dict[str, Any]:
    """Verify whether code implementing a goal exists and assess its maturity stage.

    Maturity stages: scaffolded -> candidate -> connected -> tested.
    """
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()
    gid = goal_id or f"goal_{abs(hash(statement)) % 100000}"
    assessment = client.check_goal_code_maturity(
        goal_id=gid,
        statement=statement,
        target_kind=kind,
    )
    return assessment.__dict__


# ======================================================================
# 3. EVIDENCE & CLAIMS
# ======================================================================

@_safe
async def ingest_evidence(
    case_id: str,
    content: str,
    evidence_type: Optional[str] = None,
    source_type: str = "HUMAN",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Ingest a piece of evidence (incident report, log snippet, observation, tool output)."""
    from case_engine.evidence import ingest_evidence as _ingest, _create_source
    src = await _create_source(case_id, source_type, None, metadata=metadata)
    ev = await _ingest(
        case_id=case_id,
        content=content,
        source_id=src.source_id,
        evidence_type=evidence_type,
        metadata=metadata,
    )
    return {
        "evidence_id": ev.evidence_id,
        "evidence_type": ev.evidence_type,
        "quality_score": ev.quality_score,
    }


@_safe
async def ingest_repointel_finding(case_id: str, finding: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest a RepoIntel SAST finding or bug report as TOOL_RESULT evidence into a case."""
    content = (
        f"[{finding.get('severity', 'info').upper()}] {finding.get('rule_id', 'finding')} "
        f"in {finding.get('file_path', '?')}:{finding.get('line_start', 0)}\n"
        f"{finding.get('message', '')}"
    )
    return await ingest_evidence(
        case_id=case_id,
        content=content,
        evidence_type="TOOL_RESULT",
        source_type="TOOL",
        metadata={"tool": "repointel", "finding": finding},
    )


@_safe
async def extract_claims(evidence_id: str) -> Dict[str, Any]:
    """Extract structured, verifiable claims from an ingested evidence item."""
    from case_engine.claim import extract_claims as _extract
    claims = await _extract(evidence_id)
    return {
        "count": len(claims),
        "claims": [
            {
                "claim_id": c.claim_id,
                "statement": c.statement[:250],
                "claim_type": c.claim_type,
                "confidence": c.confidence,
            }
            for c in claims
        ],
    }


@_safe
async def assess_credibility(evidence_id: str) -> Dict[str, Any]:
    """Compute five-axis credibility assessment for evidence (kind, reliability, observation, independence, relevance)."""
    from case_engine.evidence import assess_evidence_credibility
    return await assess_evidence_credibility(evidence_id)


# ======================================================================
# 4. HYPOTHESES & INDUCTION (Explanatory Generation)
# ======================================================================

@_safe
async def create_hypothesis(
    case_id: str,
    statement: str,
    initial_confidence: float = 0.5,
    origin: str = "user",
) -> Dict[str, Any]:
    """Create a candidate hypothesis for an investigation."""
    from case_engine.hypothesis import create_hypothesis as _create
    h = await _create(case_id, statement, initial_confidence, origin=origin)
    return {"hypothesis_id": h.hypothesis_id, "initial_confidence": h.initial_confidence}


@_safe
async def list_hypotheses(case_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all hypotheses for a case with current confidence scores."""
    from case_engine.hypothesis import list_hypotheses_for_case, get_current_version
    hyps = await list_hypotheses_for_case(case_id, status=status)
    out = []
    for h in hyps:
        try:
            v = await get_current_version(h.hypothesis_id)
            out.append({
                "hypothesis_id": h.hypothesis_id,
                "statement": h.statement,
                "status": h.status,
                "confidence": v.confidence if v else h.initial_confidence,
                "version": v.version_number if v else 1,
            })
        except Exception:
            out.append({
                "hypothesis_id": h.hypothesis_id,
                "statement": h.statement,
                "status": h.status,
                "confidence": h.initial_confidence,
            })
    return out


@_safe
async def induce_hypotheses(case_id: str, max_candidates: int = 6) -> Dict[str, Any]:
    """Generate competing explanatory hypotheses inductively from active case evidence.

    This generates novel explanations instead of merely retrieving existing text.
    """
    from case_engine.induction import induce_hypotheses as _induce
    return await _induce(case_id, max_candidates=max_candidates)


# ======================================================================
# 5. INVESTIGATION CYCLE
# ======================================================================

@_safe
async def run_investigation_cycle(
    case_id: str,
    enable_next_best_action: bool = True,
    enable_falsification: bool = False,
    enable_auto_execute: bool = False,
    enable_induction: bool = False,
    enable_workspace: bool = False,
) -> Dict[str, Any]:
    """Execute a full investigation reasoning cycle:

    Recomputes hypotheses, detects contradictions, checks gaps, updates decision ladder,
    and recommends the next best action.
    """
    from case_engine.cycle import run_investigation_cycle as _cycle
    return await _cycle(
        case_id=case_id,
        enable_next_best_action=enable_next_best_action,
        enable_falsification=enable_falsification,
        enable_auto_execute=enable_auto_execute,
        enable_induction=enable_induction,
        enable_workspace=enable_workspace,
    )


# ======================================================================
# 6. RESEARCH & SERENDIPITY
# ======================================================================

@_safe
async def ice_research_brief(case_id: str, max_gaps: int = 6) -> Dict[str, Any]:
    """Generate a STORM-lite multi-perspective research brief identifying information gaps."""
    from case_engine.research import research_brief as _rb
    return await _rb(case_id, max_gaps=max_gaps)


@_safe
async def ice_suggest_next(case_id: str) -> Dict[str, Any]:
    """Use the Serendipity Engine (DPP diversity + bandit exploration) to suggest diverse next items."""
    _ICE_DIR = Path(__file__).resolve().parents[2]
    _SE_DIR = _ICE_DIR / "serendipity-engine"
    if str(_SE_DIR) not in sys.path:
        sys.path.insert(0, str(_SE_DIR))
    from ice_adapter.adapter import IceSerendipityAdapter
    from case_engine.goals import list_goals

    active_goals = await list_goals(case_id, include_superseded=False)
    items = IceSerendipityAdapter.goals_to_items(active_goals)
    suggestions = [
        {"id": it.id, "title": it.title, "category": it.category, "quality": it.quality}
        for it in items[:6]
    ]
    return {"case_id": case_id, "diverse_suggestions": suggestions}


# ======================================================================
# 7. ADVANCED RETRIEVAL (Evidence Store)
# ======================================================================

@_safe
async def search_evidence(
    query: str,
    case_id: Optional[str] = None,
    cross_case: bool = False,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Hybrid search over ICE evidence (dense vector + lexical BM25 + RRF fusion)."""
    from case_engine.retrieval import search_evidence as _search
    hits = await _search(query, case_id=case_id, cross_case=cross_case, limit=limit)
    return [h.to_dict() for h in hits]


@_safe
async def contrastive_search(query: str, case_id: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Deliberately search for counter-evidence that CHALLENGES a statement or hypothesis."""
    from case_engine.retrieval import contrastive_search as _cs
    hits = await _cs(query, case_id=case_id, limit=limit)
    return [h.to_dict() for h in hits]


@_safe
async def associative_search(
    anchor_item_id: str,
    anchor_type: str = "evidence",
    hops: int = 2,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Traverse evidence graph edges from an anchor concept across multi-hop associations."""
    from case_engine.retrieval import associative_search as _as
    hits = await _as(anchor_item_id, anchor_type, hops, limit)
    return [h.to_dict() for h in hits]


@_safe
async def cross_case_connections(case_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Find analogous evidence and patterns across other investigation cases."""
    from case_engine.retrieval import find_cross_case_connections
    return await find_cross_case_connections(case_id, limit=limit)


# ======================================================================
# 8. CAUSAL & TEMPORAL ANALYSIS
# ======================================================================

@_safe
async def build_timeline(case_id: str) -> List[Dict[str, Any]]:
    """Reconstruct an ordered temporal timeline of all events and state observations in a case."""
    from case_engine.timeline import build_case_timeline
    entries = await build_case_timeline(case_id)
    return [e.to_dict() for e in entries]


@_safe
async def explain_state_change(case_id: str) -> Dict[str, Any]:
    """Locate healthy->failing state transitions and isolate candidate causes in that window."""
    from case_engine.timeline import explain_state_change as _explain
    return await _explain(case_id)


@_safe
async def build_ach_matrix(case_id: str) -> Dict[str, Any]:
    """Build Analysis of Competing Hypotheses (ACH) matrix:

    Evaluates every claim against ALL hypotheses to reveal least-contradicted
    explanations and highly discriminating evidence.
    """
    from case_engine.causal import build_ach_matrix as _ach
    return await _ach(case_id)


@_safe
async def propose_causal_edge(
    case_id: str,
    cause_label: str,
    effect_label: str,
    relation: str = "CONTRIBUTES_TO",
    cause_node_type: str = "claim",
    cause_node_id: Optional[str] = None,
    effect_node_type: str = "hypothesis",
    effect_node_id: Optional[str] = None,
    confidence: float = 0.5,
    assumptions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Propose a causal edge between observations, claims, or hypotheses."""
    from case_engine.causal import propose_causal_edge as _propose
    c_id = cause_node_id or f"node_{abs(hash(cause_label)) % 100000}"
    e_id = effect_node_id or f"node_{abs(hash(effect_label)) % 100000}"
    return await _propose(
        case_id=case_id,
        cause_node_type=cause_node_type,
        cause_node_id=c_id,
        cause_label=cause_label,
        effect_node_type=effect_node_type,
        effect_node_id=e_id,
        effect_label=effect_label,
        relation=relation,
        confidence=confidence,
        assumptions=assumptions,
    )


@_safe
async def predict_intervention(case_id: str, node_id: str) -> Dict[str, Any]:
    """Predict downstream changes if a specific causal node is intervened upon."""
    from case_engine.causal import predict_intervention as _predict
    return await _predict(case_id, node_id)


# ======================================================================
# 9. WORKSPACE & ADAPTIVE PLAN
# ======================================================================

@_safe
async def run_workspace_cycle(case_id: str, budget: int = 7) -> Dict[str, Any]:
    """Run cognitive attention competition across immediate, maintenance, development, and exploration."""
    from case_engine.workspace import run_workspace_cycle as _ws
    return await _ws(case_id, budget=budget)


@_safe
async def get_suggestions(case_id: str) -> List[Dict[str, Any]]:
    """Gather endogenous 'system noticed' alerts: aged threads, uncited evidence, stuck hypotheses."""
    from case_engine.endogenous import collect_case_endogenous_candidates
    return await collect_case_endogenous_candidates(case_id)


@_safe
async def create_plan_item(
    case_id: str,
    description: str,
    objective_ref: Optional[str] = None,
    depends_on: Optional[List[str]] = None,
    supersedes_item: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a structured plan item or replacement item to adaptive plan management."""
    from case_engine.plan import create_plan_item as _create
    return await _create(
        case_id=case_id,
        description=description,
        objective_ref=objective_ref,
        depends_on=depends_on,
        supersedes_item=supersedes_item,
    )


@_safe
async def reconcile_plan(case_id: str) -> Dict[str, Any]:
    """Compare plan items against code/evidence to detect completed, diverged, or delayed tasks."""
    from case_engine.plan import reconcile_plan as _reconcile
    return await _reconcile(case_id)


# ======================================================================
# 10. FORENSIC DIAGNOSTICS & STATE QUERIES
# ======================================================================

@_safe
async def localize_first_failure(case_id: str) -> Dict[str, Any]:
    """AgentRx failure localization: find the FIRST invariant violation in the event log."""
    from case_engine.trajectory import localize_first_failure as _loc
    return await _loc(case_id)


@_safe
async def memory_diagnostics(case_id: str, expected_facts: List[str]) -> Dict[str, Any]:
    """EvalMem diagnostic: classify memory failure per fact into encoding, retrieval, or reasoning."""
    from case_engine.diagnostics import memory_diagnostic_report
    return await memory_diagnostic_report(case_id, expected_facts)


@_safe
async def get_case_metrics(case_id: str) -> Dict[str, Any]:
    """Get runtime metrics: evidence trace coverage, investigation efficiency, and decision factors."""
    from case_engine.metrics import get_case_metrics as _gcm
    return await _gcm(case_id)


@_safe
async def validate_case(case_id: str) -> Dict[str, Any]:
    """Verify case coherence: check dangling references, orphan claims, and timeline gaps."""
    from case_engine.consistency import validate_case as _vc
    violations = await _vc(case_id)
    return {"coherent": len(violations) == 0, "violations": violations}


@_safe
async def list_contradictions(case_id: str) -> List[Dict[str, Any]]:
    """List unresolved claim/evidence contradictions for a case."""
    from case_engine.contradiction import get_active_contradictions
    contras = await get_active_contradictions(case_id)
    return [
        {
            "contradiction_id": c.contradiction_id,
            "claim_a": c.claim_a,
            "claim_b": c.claim_b,
            "severity": c.severity,
            "status": c.status,
        }
        for c in contras
    ]


@_safe
async def list_gaps(case_id: str, resolved: bool = False) -> List[Dict[str, Any]]:
    """List open information gaps where critical evidence is still missing."""
    from case_engine.information_gap import list_gaps_for_case
    gaps = await list_gaps_for_case(case_id, resolved=resolved)
    return [
        {
            "gap_id": g.gap_id,
            "needed_evidence": g.needed_evidence,
            "priority": g.priority,
            "why_needed": g.why_needed,
        }
        for g in gaps
    ]


@_safe
async def list_actions(case_id: str) -> List[Dict[str, Any]]:
    """List pending investigation actions prioritized by information gain."""
    from case_engine.action import get_pending_actions
    actions = await get_pending_actions(case_id)
    return [
        {
            "action_id": a.action_id,
            "action_type": a.action_type,
            "target": a.target,
            "status": a.status,
            "priority_estimate": a.expected_information_gain * a.decision_impact,
        }
        for a in actions
    ]


@_safe
async def decide(case_id: str) -> Dict[str, Any]:
    """Evaluate decision ladder: returns ACT, INVESTIGATE, or ABSTAIN with full rationale."""
    from case_engine.decision import decide as _decide
    d = await _decide(case_id)
    return {
        "decision_id": d.decision_id,
        "decision_type": d.decision_type,
        "confidence": d.confidence,
        "rationale": d.rationale,
        "top_factors": d.top_factors,
    }


# ======================================================================
# 11. REPOINTEL DIRECT (Offline / Direct SQLite AST Grounding)
# ======================================================================

@_safe
async def repointel_search_code(query: str, limit: int = 10) -> Dict[str, Any]:
    """Search codebase files and symbols using RepoIntel's lexical/AST index."""
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()
    hits = client.search_code(query, limit=limit)
    return {"query": query, "hits": hits, "count": len(hits)}


@_safe
async def repointel_get_callers(symbol: str) -> Dict[str, Any]:
    """Look up call graph callers and downstream dependencies of a function or class."""
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()
    callers = client.get_callers(symbol)
    return {"symbol": symbol, "callers": callers, "caller_count": len(callers)}


@_safe
async def repointel_get_callees(symbol: str, limit: int = 20) -> Dict[str, Any]:
    """Look up functions or classes called downstream by a symbol."""
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()
    callees = client.get_callees(symbol, limit=limit)
    return {"symbol": symbol, "callees": callees, "callee_count": len(callees)}


@_safe
async def repointel_get_impact(symbol: str, max_depth: int = 5, limit: int = 20) -> Dict[str, Any]:
    """Calculate recursive blast radius and upstream caller tree using SQLite Recursive CTE."""
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()
    impact = client.get_impact_radius(symbol, max_depth=max_depth, limit=limit)
    return {"symbol": symbol, "impacted_callers": impact, "impact_count": len(impact)}


@_safe
async def repointel_list_gaps(repo_id: Optional[str] = None, gap_type: str = "all", limit: int = 50) -> Dict[str, Any]:
    """List codebase gaps: test_gap, doc_gap, incomplete_feature, and error_handling_gap."""
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()
    return client.list_gaps(repo_id=repo_id, gap_type=gap_type, limit=limit)


@_safe
async def repointel_get_health_score(repo_id: Optional[str] = None) -> Dict[str, Any]:
    """Calculate aggregate codebase health score (0-100) and letter grade (A-D)."""
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()
    return client.get_health_score(repo_id=repo_id)


@_safe
async def repointel_get_architecture(repo_id: Optional[str] = None) -> Dict[str, Any]:
    """Get high-level repository architecture: core modules, component counts, and hubs."""
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()
    arch = client.get_architecture(repo_id=repo_id)
    return {"architecture": arch}


@_safe
async def repointel_get_findings(
    repo_id: Optional[str] = None,
    severity: Optional[str] = None,
) -> Dict[str, Any]:
    """Get static code analysis findings, smells, and potential bug vectors."""
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()
    findings = client.get_findings(repo_id=repo_id, severity=severity)
    return {"findings": findings, "count": len(findings)}


@_safe
async def repointel_search_symbols(
    pattern: str,
    repo_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Search AST symbols (functions, classes, methods) matching a name pattern."""
    from case_engine.repointel_client import RepoIntelClient
    client = RepoIntelClient()
    symbols = client.search_symbols(pattern=pattern, repo_id=repo_id)
    return {"symbols": symbols, "count": len(symbols)}


# ======================================================================
# 12. BRIDGING TOOLS (Inter-Agent Delegation)
# ======================================================================

@_safe
async def invoke_repointel_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke any RepoIntel tool dynamically by name over HTTP or direct DB fallback."""
    from app.mcp.repointel_bridge import call_repointel
    return await call_repointel(tool_name, arguments)


@_safe
async def analyze_code_with_repointel(
    case_id: str,
    repo_id: str,
    symbol_or_query: str,
    analysis_type: str = "impact",
) -> Dict[str, Any]:
    """Ask RepoIntel to analyze code and auto-ingest the resulting findings into ICE evidence."""
    from app.mcp.repointel_bridge import analyze_code_with_repointel as _ac
    return await _ac(case_id, repo_id, symbol_or_query, analysis_type=analysis_type)


# ======================================================================
# MASTER TOOLS REGISTRY
# ======================================================================

TOOLS: Dict[str, Dict[str, Any]] = {
    # 1. Case Lifecycle
    "create_case": {"fn": create_case, "desc": "Create a new investigation case (debug/research/explore)"},
    "get_case_summary": {"fn": get_case_summary, "desc": "Get full case summary: counts, leading hypothesis, next action"},
    "list_cases": {"fn": list_cases, "desc": "List cases with optional filters"},

    # 2. Goals & Project Model
    "ice_create_goal": {"fn": ice_create_goal, "desc": "Record a goal, subgoal, invariant, or constraint with clarity level 1-4"},
    "ice_list_goals": {"fn": ice_list_goals, "desc": "List goals, invariants, and milestones for a case"},
    "ice_reconcile_goals": {"fn": ice_reconcile_goals, "desc": "Reconcile goals against evidence to find completed or diverged goals"},
    "ice_intent_profile": {"fn": ice_intent_profile, "desc": "Get distribution of 13 intent kinds and clarity levels"},
    "ice_verify_code_maturity": {"fn": ice_verify_code_maturity, "desc": "Verify whether code implementing a goal exists on the maturity ladder"},

    # 3. Evidence
    "ingest_evidence": {"fn": ingest_evidence, "desc": "Ingest evidence (incident report, log, observation) into a case"},
    "ingest_repointel_finding": {"fn": ingest_repointel_finding, "desc": "Ingest a RepoIntel finding as TOOL_RESULT evidence"},
    "extract_claims": {"fn": extract_claims, "desc": "Extract structured claims from an evidence item"},
    "assess_credibility": {"fn": assess_credibility, "desc": "Five-axis evidence credibility assessment"},

    # 4. Hypotheses
    "create_hypothesis": {"fn": create_hypothesis, "desc": "Create a hypothesis with initial confidence"},
    "list_hypotheses": {"fn": list_hypotheses, "desc": "List hypotheses for a case with current confidence"},
    "induce_hypotheses": {"fn": induce_hypotheses, "desc": "Inductively generate competing explanations from evidence"},

    # 5. Investigation Cycle
    "run_investigation_cycle": {"fn": run_investigation_cycle, "desc": "Run full investigation cycle: recompute, contradict, decide, recommend"},

    # 6. Research & Serendipity
    "ice_research_brief": {"fn": ice_research_brief, "desc": "Generate STORM-lite multi-perspective research brief and gaps"},
    "ice_suggest_next": {"fn": ice_suggest_next, "desc": "Serendipity Engine DPP diversity and bandit suggestions"},

    # 7. Retrieval
    "search_evidence": {"fn": search_evidence, "desc": "Hybrid search over ICE evidence (vector + lexical + RRF)"},
    "contrastive_search": {"fn": contrastive_search, "desc": "Hunt for counter-evidence that CHALLENGES a statement"},
    "associative_search": {"fn": associative_search, "desc": "Traverse evidence graph edges from an anchor item"},
    "cross_case_connections": {"fn": cross_case_connections, "desc": "Find similar evidence and patterns across cases"},

    # 8. Causal & Temporal
    "build_timeline": {"fn": build_timeline, "desc": "Reconstruct ordered timeline from temporal signals"},
    "explain_state_change": {"fn": explain_state_change, "desc": "Locate events inside healthy->failing transition window"},
    "build_ach_matrix": {"fn": build_ach_matrix, "desc": "Analysis of Competing Hypotheses matrix evaluation"},
    "propose_causal_edge": {"fn": propose_causal_edge, "desc": "Propose a causal relationship between observations or claims"},
    "predict_intervention": {"fn": predict_intervention, "desc": "Predict downstream consequences of intervening on a node"},

    # 9. Workspace & Plan
    "run_workspace_cycle": {"fn": run_workspace_cycle, "desc": "Attention portfolio across 5 investigative categories"},
    "get_suggestions": {"fn": get_suggestions, "desc": "Endogenous 'system noticed' alerts and candidates"},
    "create_plan_item": {"fn": create_plan_item, "desc": "Add a plan item to adaptive plan manager"},
    "reconcile_plan": {"fn": reconcile_plan, "desc": "Compare plan items against code/implementation evidence"},

    # 10. Diagnostics & Decisions
    "localize_first_failure": {"fn": localize_first_failure, "desc": "AgentRx first invariant violation localization"},
    "memory_diagnostics": {"fn": memory_diagnostics, "desc": "EvalMem memory diagnostic: encoding, retrieval, reasoning"},
    "get_case_metrics": {"fn": get_case_metrics, "desc": "Case runtime metrics (ETC, IE, EU, decisions)"},
    "validate_case": {"fn": validate_case, "desc": "Verify case coherence and check dangling references"},
    "list_contradictions": {"fn": list_contradictions, "desc": "List unresolved contradictions in a case"},
    "list_gaps": {"fn": list_gaps, "desc": "List open information gaps"},
    "list_actions": {"fn": list_actions, "desc": "List pending investigation actions"},
    "decide": {"fn": decide, "desc": "Run decision ladder: ACT / INVESTIGATE / ABSTAIN"},

    # 11. RepoIntel Offline AST Grounding
    "repointel_search_code": {"fn": repointel_search_code, "desc": "Search codebase AST symbols and source text"},
    "repointel_get_callers": {"fn": repointel_get_callers, "desc": "Find function/class callers and downstream dependencies"},
    "repointel_get_callees": {"fn": repointel_get_callees, "desc": "Find functions/classes called downstream by a symbol"},
    "repointel_get_impact": {"fn": repointel_get_impact, "desc": "Calculate recursive blast radius and upstream caller tree using SQLite Recursive CTE"},
    "repointel_get_architecture": {"fn": repointel_get_architecture, "desc": "Get repository architecture and core components"},
    "repointel_get_findings": {"fn": repointel_get_findings, "desc": "Get static code analysis findings and smells"},
    "repointel_search_symbols": {"fn": repointel_search_symbols, "desc": "Search AST symbols matching a pattern"},
    "repointel_list_gaps": {"fn": repointel_list_gaps, "desc": "List codebase gaps (test, doc, stub, error handling)"},
    "repointel_get_health_score": {"fn": repointel_get_health_score, "desc": "Calculate codebase health score (0-100) and grade (A-D)"},
    # Backwards compatibility aliases
    "ice_repointel_search_code": {"fn": repointel_search_code, "desc": "Alias: search codebase AST symbols and source text"},
    "ice_repointel_callers": {"fn": repointel_get_callers, "desc": "Alias: find function/class callers and downstream dependencies"},
    "ice_repointel_architecture": {"fn": repointel_get_architecture, "desc": "Alias: get repository architecture and core components"},

    # 12. Bridge Tools
    "invoke_repointel_tool": {"fn": invoke_repointel_tool, "desc": "Invoke any RepoIntel tool dynamically by name"},
    "analyze_code_with_repointel": {"fn": analyze_code_with_repointel, "desc": "Ask RepoIntel to analyze code and auto-ingest into ICE evidence"},
}


def get_mcp_schemas() -> List[Dict[str, Any]]:
    """Generate standardized JSON Schema definitions for all registered tools."""
    schemas = []
    for name, info in TOOLS.items():
        fn = info["fn"]
        # Unwrap if decorated with _safe
        target_fn = getattr(fn, "__wrapped__", fn)
        sig = inspect.signature(target_fn)
        props: Dict[str, Any] = {}
        required: List[str] = []

        for pname, param in sig.parameters.items():
            if pname == "case_id":
                props[pname] = {"type": "string", "description": "Target Case ID"}
                if param.default is inspect.Parameter.empty:
                    required.append(pname)
            elif param.default is inspect.Parameter.empty:
                props[pname] = {"type": "string"}
                required.append(pname)
            elif isinstance(param.default, bool):
                props[pname] = {"type": "boolean", "default": param.default}
            elif isinstance(param.default, int):
                props[pname] = {"type": "integer", "default": param.default}
            elif isinstance(param.default, float):
                props[pname] = {"type": "number", "default": param.default}
            elif isinstance(param.default, str):
                props[pname] = {"type": "string", "default": param.default}
            elif isinstance(param.default, (list, tuple)):
                props[pname] = {"type": "array", "default": list(param.default)}
            elif isinstance(param.default, dict):
                props[pname] = {"type": "object", "default": param.default}
            else:
                props[pname] = {"type": "string"}

        schemas.append({
            "name": name,
            "description": info["desc"],
            "inputSchema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        })
    return schemas
