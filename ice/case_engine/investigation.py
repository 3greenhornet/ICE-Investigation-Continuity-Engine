"""case_engine/investigation.py — Backward-compatibility shim.

The canonical location of run_investigation_cycle is now case_engine.cycle
(renamed to match the Oct 23 codebase — merge spec Part 3 §0.6 Option B).
This module re-exports the function so existing imports
(`from case_engine.investigation import run_investigation_cycle`) continue
to work.

Do not add new code here. All changes go in case_engine.cycle.
"""
from case_engine.cycle import run_investigation_cycle

__all__ = ["run_investigation_cycle"]