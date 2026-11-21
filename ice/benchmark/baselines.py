"""benchmark/baselines.py — Baselines B1–B6 (spec §17.4).

Owner:      ICE revamp, Part 4 §17.4 (baselines).
Purpose:    Comparison targets for the full engine:
                B1  recent-history LLM only        (no engine state at all)
                B2  vector RAG over engine.memory  (retrieval, no reasoning)
                B3  case-summary LLM               (compressed engine state)
                B4  engine without contradictions  (ENABLE_CONTRADICTIONS=false)
                B5  engine without versioning      (ENABLE_HYPOTHESIS_VERSIONING=false)
                B6  the full engine                (run_engine_on_case)
            BASELINES maps "B1".."B6" to the callables for the CLI
            (`ice benchmark baselines`).
Deps:       case_engine.llm / case_engine.case (public API), engine.memory
            substrate for B2, benchmark.runner for B4–B6.

Call contract: every baseline is ``async def(case_ref: str, question: str)
-> str`` (B1 also accepts ``history``). B1–B3 treat case_ref as an existing
case_id (B1 ignores it; B2 uses it as the retrieval session filter; B3
requires a real case). B4–B6 resolve case_ref to a benchmark case
DIRECTORY via _resolve_case_dir — either a path containing case.json or a
name under benchmark/cases/ (e.g. "case_01_latency").

Advisory-flag note (B4/B5): RESOLVED — the engine now reads both flags at
CALL time (case_engine/contradiction.py returns None without loading
claims or calling the LLM; case_engine/hypothesis.py freezes confidences
with no version rows), so B4/B5 are real ablations. The baselines still
save and restore prior values in try/finally — an exception mid-run must
never leak a mutated environment (the spec's bare set/"true" reset would
also clobber a user-chosen value; this is the safe transcription).
"""

import os
from typing import Awaitable, Callable, Optional

from case_engine.llm import _call_llm_text

# benchmark/cases/ next to this file — used by _resolve_case_dir.
_BENCHMARK_CASES_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cases"
)


# ---------------------------------------------------------------------------
# Environment flag helpers (shared with benchmark/ablation.py)
# ---------------------------------------------------------------------------

def set_env_flags(flags: dict[str, bool]) -> dict[str, Optional[str]]:
    """Force env flags; return the saved prior values for restore_env_flags.

    Preconditions: every key is a non-empty env var name.
    Postconditions: os.environ[key] == "true"/"false" per the bool values.
    Raises: nothing.
    Side effects: mutates os.environ (caller MUST restore).
    """
    saved: dict[str, Optional[str]] = {}
    for key, value in flags.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = "true" if value else "false"
    return saved


def restore_env_flags(saved: dict[str, Optional[str]]) -> None:
    """Restore prior env values saved by set_env_flags (None -> unset)."""
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _resolve_case_dir(case_ref: str) -> str:
    """Resolve a case reference to a benchmark case directory.

    Accepts (a) a path to a directory containing case.json, or (b) the name
    of a directory under benchmark/cases/ (e.g. "case_01_latency").
    Anything else is returned unchanged so run_engine_on_case raises the
    natural FileNotFoundError from case.json handling.
    """
    if os.path.isfile(os.path.join(case_ref, "case.json")):
        return case_ref
    sibling = os.path.join(_BENCHMARK_CASES_ROOT, case_ref)
    if os.path.isfile(os.path.join(sibling, "case.json")):
        return sibling
    return case_ref


# ---------------------------------------------------------------------------
# B1–B3: LLM-centric baselines
# ---------------------------------------------------------------------------

async def baseline_1_llm_recent(
    case_id: str, question: str, history: str = ""
) -> str:
    """B1: answer from the recent conversation history alone (no engine)."""
    prompt = (
        f"Conversation so far:\n{history}\n\n"
        f"Question: {question}\n\n"
        "What is the root cause? Answer concisely."
    )
    return (await _call_llm_text(prompt)) or ""


async def baseline_2_vector_rag(case_id: str, question: str) -> str:
    """B2: vector-RAG over engine.memory, then a single LLM answer."""
    from engine.memory import retrieve_candidates_hybrid

    candidates = await retrieve_candidates_hybrid(
        query=question,
        session_id=case_id,
        current_turn=1,
        state_drives={},
        limit=10,
    )
    context = "\n\n".join(c.content[:500] for c in candidates)
    prompt = (
        f"Evidence:\n{context}\n\n"
        f"Question: {question}\n\n"
        "What is the root cause?"
    )
    return (await _call_llm_text(prompt)) or ""


async def baseline_3_case_summary(case_id: str, question: str) -> str:
    """B3: answer from the compressed case summary (get_case_summary)."""
    from case_engine.case import get_case_summary

    summary = await get_case_summary(case_id)
    prompt = f"Case summary: {summary}\n\nQuestion: {question}"
    return (await _call_llm_text(prompt)) or ""


# ---------------------------------------------------------------------------
# B4–B6: engine baselines
# ---------------------------------------------------------------------------

async def baseline_4_no_contradictions(case_id: str, question: str) -> str:
    """B4: full engine with ENABLE_CONTRADICTIONS forced false.

    See the module docstring: the flag is currently advisory (the engine
    does not read it), but it is set and restored safely regardless.
    """
    from benchmark.runner import run_engine_on_case

    saved = set_env_flags({"ENABLE_CONTRADICTIONS": False})
    try:
        result = await run_engine_on_case(_resolve_case_dir(case_id))
    finally:
        restore_env_flags(saved)
    return result.get("leading_hypothesis_statement", "")


async def baseline_5_no_versioning(case_id: str, question: str) -> str:
    """B5: full engine with ENABLE_HYPOTHESIS_VERSIONING forced false.

    See the module docstring: the flag is currently advisory (the engine
    does not read it), but it is set and restored safely regardless.
    """
    from benchmark.runner import run_engine_on_case

    saved = set_env_flags({"ENABLE_HYPOTHESIS_VERSIONING": False})
    try:
        result = await run_engine_on_case(_resolve_case_dir(case_id))
    finally:
        restore_env_flags(saved)
    return result.get("leading_hypothesis_statement", "")


async def baseline_6_full_engine(case_id: str, question: str) -> str:
    """B6: the full engine (run_engine_on_case) — the system under test."""
    from benchmark.runner import run_engine_on_case

    result = await run_engine_on_case(_resolve_case_dir(case_id))
    return result.get("leading_hypothesis_statement", "")


# ---------------------------------------------------------------------------
# Registry (for the CLI `ice benchmark baselines` group)
# ---------------------------------------------------------------------------

BASELINES: dict[str, Callable[..., Awaitable[str]]] = {
    "B1": baseline_1_llm_recent,
    "B2": baseline_2_vector_rag,
    "B3": baseline_3_case_summary,
    "B4": baseline_4_no_contradictions,
    "B5": baseline_5_no_versioning,
    "B6": baseline_6_full_engine,
}