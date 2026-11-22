"""case_engine/pedagogy.py — Backward-compatibility shim mapping to case_engine/code_understanding.py.

Owner:      ICE Developer Understanding System.
Purpose:    Preserves backward compatibility for any existing callers while the
            canonical software-focused implementation lives in code_understanding.py.
"""

from case_engine.code_understanding import (
    CodeUnderstandingEngine as PedagogyEngine,
    DeveloperBracket as BracketResult,
    CodeUnderstandingPlan as PlanDAG,
    CodeUnderstandingNode as PlanNode,
    SocraticStep as TeachingStep,
    SocraticPracticeChallenge as PracticeProblem,
    HINT_RUNGS,
)

__all__ = [
    "PedagogyEngine",
    "BracketResult",
    "PlanDAG",
    "PlanNode",
    "TeachingStep",
    "PracticeProblem",
    "HINT_RUNGS",
]
