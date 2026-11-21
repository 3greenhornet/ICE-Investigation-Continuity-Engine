"""case_engine/action_value_estimator.py — ActionValueEstimator facade.

Owner:      ICE revamp, audit gap #21 (PDF page 145 names this file as the
            semantic replacement for the legacy engine/generativity_estimator.py
            "GenerativityEstimator"; the spec placed the actual logic in
            next_best_action.py and omitted the file entirely).
Purpose:    Thin re-export module. All action-value scoring lives in
            case_engine.next_best_action.py; this module exists so callers
            (and the PDF's module map) can import the ActionValueEstimator
            API from its canonical filename. No logic is defined here — do
            not add any; edit next_best_action.py instead.
Deps:       case_engine.next_best_action (re-export only).
"""

from case_engine.next_best_action import (
    compute_eig,
    compute_decision_impact,
    compute_falsification_value,
    compute_source_diversity_bonus,
    compute_redundancy_penalty,
    compute_priority,
    compute_priority_with_factors,
    select_next_best_action,
)

__all__ = [
    "compute_eig",
    "compute_decision_impact",
    "compute_falsification_value",
    "compute_source_diversity_bonus",
    "compute_redundancy_penalty",
    "compute_priority",
    "compute_priority_with_factors",
    "select_next_best_action",
]