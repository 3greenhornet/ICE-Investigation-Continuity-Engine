"""case_engine/orchestrator.py — single-entry conversational orchestrator for ICE.

Turns one natural-language message (problem, observation, or status question)
into a coordinated pass over ICE's existing planes:
    evidence ingestion -> claim extraction -> RepoIntel code lookup ->
    investigation cycle (induction + workspace + next-best-action) ->
    one coherent response.

Strictly obeys AGENTS.md four-plane rules:
- Lives inside case_engine/ and never imports from app/, cli/, or ui/.
- All state mutations go through case_engine.evidence.ingest_evidence,
  case_engine.claim.extract_claims, and case_engine.cycle.run_investigation_cycle.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_STATUS_QUERY_RE = re.compile(
    r"^\s*(?:status|summary|recap|overview|where\s+are\s+we|"
    r"what(?:'s|\s+is)\s+(?:the\s+)?(?:status|current\s+state|happening|next))\s*[?!.]*\s*$",
    re.IGNORECASE,
)


def is_status_only_query(message: str) -> bool:
    """True when the user is only asking for a status recap, not adding new info."""
    return bool(_STATUS_QUERY_RE.match(message or ""))


def _gather_code_context(query: str, repo_id: Optional[str] = None) -> list[dict]:
    """Best-effort, read-only code context from RepoIntel's SQLite KB."""
    try:
        from case_engine.repointel_client import RepoIntelClient

        client = RepoIntelClient()
        if not client.is_available:
            return []
        if hasattr(client, "search_code"):
            hits = client.search_code(query, limit=4) or []
            return [
                {
                    "file_path": h.get("file_path") or h.get("path"),
                    "symbol": h.get("symbol") or h.get("name"),
                    "snippet": str(h.get("snippet") or h.get("summary") or "")[:240],
                }
                for h in hits[:4]
            ]
    except Exception as exc:
        logger.debug("orchestrator code context skipped: %s", exc)
    return []


def _format_deterministic_answer(
    mode: str,
    leading: Optional[dict],
    claims_count: int,
    contradictions: list,
    gaps: list,
    recommended: Optional[dict],
    code_context: list[dict],
) -> str:
    parts: list[str] = []
    if mode == "status":
        parts.append("Here is the current investigation state:")
    else:
        parts.append(f"Recorded your input and extracted {claims_count} claim(s).")

    if leading:
        conf = leading.get("confidence")
        conf_str = f" (confidence {conf:.2f})" if isinstance(conf, (int, float)) else ""
        parts.append(f"Leading hypothesis{conf_str}: {leading.get('statement', '')}")
    else:
        parts.append("No leading hypothesis yet; more evidence is needed.")

    if contradictions:
        parts.append(f"Active contradictions: {len(contradictions)}.")
    if gaps:
        parts.append(f"Unresolved information gaps: {len(gaps)}.")
    if code_context:
        files = ", ".join(str(c.get("file_path") or c.get("symbol") or "?") for c in code_context[:3])
        parts.append(f"Relevant code locations from RepoIntel: {files}.")
    if recommended:
        parts.append(
            f"Recommended next step [{recommended.get('action_type', 'ACTION')}]: "
            f"{recommended.get('target', '')} — {recommended.get('rationale', '')}".strip(" —")
        )
    return "\n".join(parts)


async def _synthesize_answer(
    message: str,
    mode: str,
    leading: Optional[dict],
    claims_count: int,
    contradictions: list,
    gaps: list,
    recommended: Optional[dict],
    code_context: list[dict],
) -> str:
    fallback = _format_deterministic_answer(
        mode, leading, claims_count, contradictions, gaps, recommended, code_context
    )
    try:
        from case_engine.llm import _call_llm_text, llm_available

        if not llm_available():
            return fallback
        prompt = (
            f"User message: {message[:1200]}\n"
            f"Mode: {mode}\n"
            f"Claims extracted: {claims_count}\n"
            f"Leading hypothesis: {leading}\n"
            f"Contradictions count: {len(contradictions)}\n"
            f"Unresolved gaps count: {len(gaps)}\n"
            f"Relevant code: {code_context[:3]}\n"
            f"Recommended next action: {recommended}\n\n"
            "Write a concise, direct 3-5 sentence engineering response summarizing "
            "what ICE found, what remains uncertain, and what you recommend doing next "
            "(ask for approval only if the recommended action is consequential)."
        )
        text = await _call_llm_text(
            prompt=prompt,
            system_prompt="You are ICE, a concise, evidence-grounded engineering investigator.",
            temperature=0.2,
            role="GENERATOR",
            operation="orchestrate_reply",
            max_retries=1,
            timeout=30.0,
        )
        return (text or "").strip() or fallback
    except Exception as exc:
        logger.debug("orchestrator LLM synthesis fallback: %s", exc)
        return fallback


async def orchestrate_input(
    case_id: str,
    message: str,
    repo_id: Optional[str] = None,
    enable_falsification: bool = False,
) -> dict[str, Any]:
    """Single-entry conversational orchestrator for ICE."""
    from case_engine.case import get_case
    from case_engine.claim import extract_claims
    from case_engine.contradiction import get_active_contradictions
    from case_engine.cycle import run_investigation_cycle
    from case_engine.evidence import _create_source, ingest_evidence
    from case_engine.hypothesis import list_hypotheses_for_case
    from case_engine.information_gap import list_gaps_for_case
    from case_engine.next_best_action import select_next_best_action

    await get_case(case_id)
    text = (message or "").strip()
    code_context = _gather_code_context(text, repo_id=repo_id)

    if is_status_only_query(text):
        hyps = await list_hypotheses_for_case(case_id)
        leading = None
        if hyps:
            best = max(hyps, key=lambda h: getattr(h, "confidence", 0.0) or 0.0)
            leading = {
                "hypothesis_id": best.hypothesis_id,
                "statement": best.statement,
                "confidence": best.confidence,
            }
        contradictions = await get_active_contradictions(case_id)
        gaps = await list_gaps_for_case(case_id, resolved=False)
        rec = await select_next_best_action(case_id)
        rec_dict = rec.model_dump(mode="json") if hasattr(rec, "model_dump") else rec
        answer = await _synthesize_answer(
            text, "status", leading, 0, contradictions, gaps, rec_dict, code_context
        )
        return {
            "case_id": case_id,
            "mode": "status",
            "answer": answer,
            "evidence_id": None,
            "claims_extracted": 0,
            "leading_hypothesis": leading,
            "contradictions": [getattr(c, "contradiction_id", str(c)) for c in contradictions],
            "unresolved_gaps": [getattr(g, "gap_id", str(g)) for g in gaps],
            "recommended_action": rec_dict,
            "needs_approval": bool(
                rec_dict and rec_dict.get("action_type") in ("LAB_PROPOSE", "APPLY_PATCH", "EDIT")
            ),
            "code_context": code_context,
            "cycle": None,
        }

    src = await _create_source(
        case_id,
        "HUMAN",
        "orchestrator://user_input",
        metadata={"origin": "orchestrator", "repo_id": repo_id},
    )
    ev = await ingest_evidence(
        case_id=case_id,
        content=text,
        source_id=src.source_id,
        evidence_type="OBSERVATION",
        metadata={"origin": "orchestrator", "repo_id": repo_id},
    )

    claims_extracted = 0
    try:
        claims = await extract_claims(ev.evidence_id)
        claims_extracted = len(claims or [])
    except Exception as exc:
        logger.info("orchestrator claim extraction skipped: %s", exc)

    cycle = await run_investigation_cycle(
        case_id,
        enable_next_best_action=True,
        enable_falsification=enable_falsification,
        enable_auto_execute=False,
        enable_induction=True,
        enable_workspace=True,
    )

    leading = cycle.get("leading_hypothesis")
    contradictions = cycle.get("new_contradictions") or []
    gaps = cycle.get("new_gaps") or []
    rec_dict = cycle.get("recommended_action")
    answer = await _synthesize_answer(
        text, "investigate", leading, claims_extracted, contradictions, gaps, rec_dict, code_context
    )

    return {
        "case_id": case_id,
        "mode": "investigate",
        "answer": answer,
        "evidence_id": ev.evidence_id,
        "claims_extracted": claims_extracted,
        "leading_hypothesis": leading,
        "hypothesis_changes": cycle.get("hypothesis_changes", []),
        "contradictions": contradictions,
        "unresolved_gaps": gaps,
        "recommended_action": rec_dict,
        "needs_approval": bool(
            rec_dict and rec_dict.get("action_type") in ("LAB_PROPOSE", "APPLY_PATCH", "EDIT")
        ),
        "code_context": code_context,
        "cycle": cycle,
    }
