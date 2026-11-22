"""ice/companion.py — Interactive Zero-Cost Continuous Development Companion.

Owner:      ICE × RepoIntel Integration.
Purpose:    Acts as the intelligent bridge between ICE (Goals, Intent, Clarity,
            Research, Serendipity) and RepoIntel (AST parsing, Call Graphs,
            Symbols, Static Analysis).

Features:
    - Designed specifically for IN-PROGRESS / UNFINISHED projects.
    - Zero-cost: works with free-tier APIs (Google Gemini, Groq), local Ollama,
      or deterministic offline mode (zero API keys required).
    - Prevents false alarms on half-written code by grounding findings against
      goal clarity levels.
    - Answers:
        * "What should I build next?"
        * "Where does this feature connect?"
        * "What open questions remain before writing code?"
        * "What are the unexplored perspectives or diverse ideas?"

Usage:
    python ice/companion.py
    python ice/companion.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ICE_DIR = Path(__file__).resolve().parent
if str(_ICE_DIR) not in sys.path:
    sys.path.insert(0, str(_ICE_DIR))

from case_engine.repointel_client import RepoIntelClient  # noqa: E402
from case_engine.pedagogy import PedagogyEngine  # noqa: E402

logger = logging.getLogger("ice.companion")


class DevelopmentCompanion:
    """Zero-cost autonomous coordinator orchestrating ICE, RepoIntel, and Pedagogy."""

    def __init__(self, case_id: str = "case_default"):
        self.case_id = case_id
        self.repointel = RepoIntelClient()
        self.pedagogy = PedagogyEngine(repointel_client=self.repointel)

    def get_status_overview(self) -> Dict[str, Any]:
        """Aggregate high-level status across ICE and RepoIntel."""
        arch = self.repointel.get_architecture() if self.repointel.is_available else {}
        repos = self.repointel.list_repositories() if self.repointel.is_available else []
        return {
            "case_id": self.case_id,
            "repointel_connected": self.repointel.is_available,
            "indexed_repositories": [r["name"] for r in repos],
            "codebase_architecture": arch,
        }

    def advise_next_step(self, goal_statement: str, clarity_level: int = 2) -> Dict[str, Any]:
        """Synthesize ICE goal clarity with RepoIntel code reality.

        If the project/goal is at clarity level 1 or 2 (vague / emerging),
        it treats missing callers as EXPECTED rather than a defect, and guides
        the developer on what scaffolding to build first.
        """
        # 1. Ask RepoIntel for AST and call edge reality
        maturity = self.repointel.check_goal_code_maturity("g_temp", goal_statement)

        # 2. Formulate guidance
        actions = []
        if maturity.suggested_maturity == "no_candidate":
            if clarity_level <= 2:
                status_note = "Goal is in early exploratory stage (Clarity 1-2). No matching symbols exist yet."
                actions.append(f"Define initial data model or interface for '{goal_statement}'.")
                actions.append("Draft intent profile open questions to confirm requirements before coding.")
            else:
                status_note = "High clarity goal (Clarity 3-4) has no candidate code implementation!"
                actions.append(f"Scaffold core class/function implementing '{goal_statement}'.")
        elif maturity.suggested_maturity == "candidate":
            status_note = f"Candidate code exists ({', '.join(maturity.candidate_symbols[:2])}), but is NOT yet wired into application callers."
            actions.append(f"Connect candidate symbols to API router or workflow dispatchers.")
            actions.append("Verify if any callers are awaiting integration.")
        elif maturity.suggested_maturity == "connected":
            status_note = f"Code is wired and called by {len(maturity.callers_found)} callers, but lacks automated tests."
            actions.append("Write unit or integration tests to advance goal to 'tested' stage.")
        elif maturity.suggested_maturity == "tested":
            status_note = f"Code is implemented, connected, and tested ({', '.join(maturity.tests_found[:2])})."
            actions.append("Run verification pass to record runtime observations.")

        return {
            "goal": goal_statement,
            "clarity_level": clarity_level,
            "code_maturity": maturity.suggested_maturity,
            "candidate_symbols": maturity.candidate_symbols,
            "callers_found": maturity.callers_found,
            "tests_found": maturity.tests_found,
            "status_diagnosis": status_note,
            "recommended_actions": actions,
        }

    def suggest_research_perspectives(self, topic: str) -> List[Dict[str, str]]:
        """STORM-lite perspective discovery to help beginners and pros explore."""
        # Standard analytical dimensions for software systems
        dimensions = [
            ("Reliability & Failure Modes", "What happens when network timeouts, partial writes, or corrupted state occur?"),
            ("Security & Boundary Checks", "Who authorizes this call, and are inputs validated before processing?"),
            ("Performance & Resource Bounds", "What is the memory and latency footprint under high concurrent load?"),
            ("Ergonomics & API Simplicity", "Can a junior developer understand and invoke this interface without errors?"),
            ("Operability & Debuggability", "When this fails at 3 AM in production, what logs or traces will explain why?"),
        ]
        return [
            {"perspective": d[0], "inquiry": d[1]}
            for d in dimensions
        ]

    # Pedagogical Scaffolding & Anti-Blind-Copy-Paste Methods
    def probe_understanding(self, statement: str, thinking_trace: str = "") -> Dict[str, Any]:
        """Assess developer edge and mental models to prevent blind copy-pasting."""
        bracket = self.pedagogy.probe_learner_edge(statement, thinking_trace)
        return bracket.model_dump()

    def build_learning_plan(self, goal_statement: str, learner_level: str = "intermediate") -> Dict[str, Any]:
        """Generate a Minimal Dependency Tree DAG applying Principle i and Principle ii."""
        plan = self.pedagogy.plan_minimal_tree(goal_statement, learner_level)
        return plan.model_dump()

    def explain_why_code_exists(self, symbol: str) -> Dict[str, Any]:
        """3Blue1Brown-style Motivated Architectural Walkthrough for a symbol."""
        return self.pedagogy.explain_why_this_code_exists(symbol)

    def practice_problem(self, node_id: str, mode: str = "discrimination") -> Dict[str, Any]:
        """Generate active retrieval practice challenge with hidden solution and 6-rung hint ladder."""
        prob = self.pedagogy.generate_practice_problem(node_id, mode)
        return prob.model_dump()

    def get_hint(self, problem_id: str, rung: Optional[int] = None, thinking_trace: str = "") -> Dict[str, Any]:
        """Get the next minimal hint from the 6-rung ladder."""
        return self.pedagogy.get_hint(problem_id, rung, thinking_trace)


def print_banner():
    print("=" * 72)
    print("  ICE × REPOINTEL — CONTINUOUS DEVELOPMENT & LEARNING COMPANION")
    print("  Zero-Cost Guidance, Pedagogical Scaffolding & Anti-Copy-Paste Mode")
    print("=" * 72)


def interactive_loop():
    print_banner()
    companion = DevelopmentCompanion()
    status = companion.get_status_overview()

    print(f"\n[Status] RepoIntel Connected : {status['repointel_connected']}")
    if status['indexed_repositories']:
        print(f"[Status] Repositories Active : {', '.join(status['indexed_repositories'])}")
    if status['codebase_architecture'].get('file_count'):
        print(f"[Status] Codebase Scope      : {status['codebase_architecture']['file_count']} files, "
              f"{status['codebase_architecture'].get('call_edges_count', 0)} call edges")

    print("\nInvestigation & Code Commands:")
    print("  next <goal text>     - Check code maturity & get actionable next steps")
    print("  perspectives <topic> - Discover STORM-lite research perspectives & gaps")
    print("  search <query>       - Search RepoIntel AST symbols & callers")
    print("  hubs                 - View top architectural hub functions in the codebase")
    print("\nPedagogical & Learning Commands (LearnHarness Scaffolding):")
    print("  probe <your thoughts>- Bracket your understanding edge & check for blind copy-paste")
    print("  learn <goal>         - Generate Minimal Dependency Tree DAG (Mermaid)")
    print("  why <symbol>         - Motivated discovery: Why does this code exist?")
    print("  practice <topic>     - Active retrieval challenge with 6-rung hint ladder")
    print("  hint <prob_id>       - Get the next minimal hint rung (R0 to R5)")
    print("  exit                 - Quit companion\n")

    last_problem_id = None

    while True:
        try:
            line = input("companion> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not line:
            continue
        if line.lower() in ("exit", "quit"):
            print("Session ended.")
            break

        parts = line.split(" ", 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "next":
            if not arg:
                print("Usage: next <goal statement or feature name>")
                continue
            adv = companion.advise_next_step(arg)
            print(f"\n[Diagnosis] {adv['status_diagnosis']}")
            print(f"[Maturity ] Stage: {adv['code_maturity'].upper()}")
            if adv['candidate_symbols']:
                print(f"[AST Code ] Found: {', '.join(adv['candidate_symbols'][:3])}")
            if adv['callers_found']:
                print(f"[Callers  ] Active: {', '.join(adv['callers_found'][:3])}")
            print("\nRecommended Next Steps:")
            for i, act in enumerate(adv['recommended_actions'], 1):
                print(f"  {i}. {act}")
            print()

        elif cmd == "perspectives":
            topic = arg or "Software Architecture"
            persp = companion.suggest_research_perspectives(topic)
            print(f"\nSTORM-lite Analytical Perspectives for '{topic}':")
            for p in persp:
                print(f"  * {p['perspective']}: {p['inquiry']}")
            print()

        elif cmd == "search":
            if not arg:
                print("Usage: search <symbol or keyword>")
                continue
            syms = companion.repointel.search_symbols(arg, limit=5)
            print(f"\nFound {len(syms)} symbols matching '{arg}':")
            for s in syms:
                print(f"  - [{s.entity_type}] {s.qualified_name} in {s.file_path} (callers: {s.callers_count})")
            print()

        elif cmd == "hubs":
            arch = companion.repointel.get_architecture()
            hubs = arch.get("top_hubs", [])
            print(f"\nTop Architectural Hubs (Most Called Symbols):")
            for h in hubs[:7]:
                print(f"  * {h['name']} ({h['file']}) — {h['callers']} callers")
            print()

        elif cmd == "probe":
            if not arg:
                print("Usage: probe <describe your understanding or what you are trying to do>")
                continue
            res = companion.probe_understanding(arg)
            print(f"\n[Pedagogy Probe] Developer Level: {res['learner_level'].upper()}")
            print(f"[Cognitive Mode ] {res['cognitive_mode'].upper()}")
            print(f"[Floor / Held   ] {res['floor']}")
            print(f"[Ceiling / Edge ] {res['ceiling']}")
            print(f"[Diagnosis      ] {res['thinking_diagnosis']}")
            print("\nRecommendations:")
            for r in res['recommendations']:
                print(f"  * {r}")
            print()

        elif cmd == "learn":
            if not arg:
                print("Usage: learn <goal or concept you want to master>")
                continue
            plan = companion.build_learning_plan(arg)
            print(f"\n[Plan DAG] ID: {plan['plan_id']}")
            print(f"Minimal Dependency Tree ({len(plan['nodes'])} landmarks):")
            for n in plan['nodes']:
                print(f"  [{n['kind'].upper()[:4]}] {n['id']} : {n['title']}")
                print(f"         prereqs: {', '.join(n['prerequisites']) or 'NONE (foundation)'}")
            print("\nMermaid Diagram:")
            print(plan['mermaid_graph'])
            print()

        elif cmd == "why":
            if not arg:
                print("Usage: why <code symbol or function name>")
                continue
            why = companion.explain_why_code_exists(arg)
            print(f"\n[Why This Code Exists] Symbol: {why['symbol']}")
            print(f"[File Path           ] {why['file_path']}")
            print(f"[Grounding           ] Callers: {why['codebase_grounding']['callers']}")
            print(f"[Grounding           ] Blast: {why['codebase_grounding']['blast_radius_affected_symbols']} symbols")
            print(f"\nPrinciple i (Invariant):")
            print(f"  {why['principle_i_invariant']}")
            print(f"\nPrinciple ii (Motivated Discovery):")
            print(f"  {why['principle_ii_motivated_discovery']}")
            print(f"\nAnti-Copy-Paste Advice:")
            print(f"  {why['anti_copy_paste_advice']}")
            print()

        elif cmd == "practice":
            topic = arg or "core-invariants"
            prob = companion.practice_problem(topic)
            last_problem_id = prob['problem_id']
            print(f"\n[Practice Challenge] ID: {prob['problem_id']} (Mode: {prob['mode'].upper()})")
            print(f"Problem:\n{prob['statement']}")
            print(f"\nHint ladder ready (6 rungs: R0 to R5). Type 'hint {prob['problem_id']}' if stuck.")
            print()

        elif cmd == "hint":
            target_id = arg or last_problem_id
            if not target_id:
                print("Usage: hint <problem_id>")
                continue
            h = companion.get_hint(target_id)
            if "error" in h:
                print(f"[Error] {h['error']}")
            else:
                print(f"\n[{h['rung_label']}] {h['hint']}")
                print(f"Remaining rungs: {h['remaining_rungs']}")
                print(f"Advice: {h['doctrine_reminder']}\n")

        else:
            print(f"Unknown command '{cmd}'. Try 'next', 'probe', 'learn', 'why', 'practice', 'hint', 'search', or 'hubs'.")


def main():
    parser = argparse.ArgumentParser(description="ICE × RepoIntel Development Companion")
    parser.add_argument("--advise", default=None, help="One-shot advice for a goal")
    parser.add_argument("--case", default="case_default", help="Case ID")
    args = parser.parse_args()

    if args.advise:
        comp = DevelopmentCompanion(case_id=args.case)
        result = comp.advise_next_step(args.advise)
        print(json.dumps(result, indent=2))
        return

    interactive_loop()


if __name__ == "__main__":
    main()
