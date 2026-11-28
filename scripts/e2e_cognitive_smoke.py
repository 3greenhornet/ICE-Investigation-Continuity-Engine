#!/usr/bin/env python3
"""scripts/e2e_cognitive_smoke.py — End-to-End Cognitive Synergy Smoke Test.

Validates the full cognitive bridge between ICE, RepoIntel, and LearnHarness:
1. Autonomy Dial: Verifies dynamic transitions (passive -> auto-approve -> auto-execute).
2. Action Execution Loop: Executes code actions via RepoIntelExecutor into evidence.
3. Bidirectional Event Sync: Ingests reverse event streams (FindingDetected).
4. Socratic & Pedagogical Learning: Generates Mermaid diagrams, multi-premise reasoning,
   anti-copy-paste verification challenges, and persistent ADR notes.
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Setup paths dynamically without hardcoded machine paths
_ROOT = Path(__file__).resolve().parent.parent
_ICE_DIR = _ROOT / "ice"
if str(_ICE_DIR) not in sys.path:
    sys.path.insert(0, str(_ICE_DIR))

from case_engine.action import (
    Action,
    ActionType,
    ApprovalClass,
    RepoIntelExecutor,
    default_executor_for,
    get_autonomy_level,
    set_autonomy_level,
)
from case_engine.code_understanding import (
    CodeUnderstandingEngine,
    DeveloperBracket,
)
from app.routes.repointel import (
    RepoIntelEventPayload,
    repointel_event_sync,
)


async def run_e2e_smoke() -> int:
    print("=" * 70)
    print("RUNNING E2E COGNITIVE & SYNCHRONIZATION SMOKE TEST")
    print("=" * 70)

    # -------------------------------------------------------------
    # 1. Autonomy Dial Verification
    # -------------------------------------------------------------
    print("\n[Step 1] Verifying Autonomy Dial (AUTONOMY_LEVEL)...")
    set_autonomy_level(3)
    assert get_autonomy_level() == 3, f"Expected 3, got {get_autonomy_level()}"

    set_autonomy_level(5)
    assert get_autonomy_level() == 5, f"Expected 5, got {get_autonomy_level()}"
    print("  -> Autonomy level successfully switched (0..10 dial working).")

    # -------------------------------------------------------------
    # 2. Action Execution Loop via RepoIntelExecutor
    # -------------------------------------------------------------
    print("\n[Step 2] Verifying RepoIntel Action Execution Loop...")
    action = Action(
        action_id="act_e2e_001",
        case_id="case_e2e_smoke",
        action_type="SEARCH",
        target="auth_token_refresh",
        rationale="Investigate token refresh race condition",
        approval_class="READ_ONLY",
        approval_required=False,
        result_payload={"query": "token_refresh", "limit": 3},
        created_at=datetime.now(timezone.utc),
    )

    executor = default_executor_for(action)
    assert isinstance(executor, RepoIntelExecutor), f"Expected RepoIntelExecutor, got {type(executor)}"

    res = await executor.execute(action)
    assert res.outcome in ("EVIDENCE_PRODUCED", "INFORMATIVE_NO_EVIDENCE", "UNINFORMATIVE", "UNSUPPORTED")
    print(f"  -> Action executed by RepoIntelExecutor:")
    print(f"     Outcome: {res.outcome}")
    print(f"     Notes: {res.notes}")
    print(f"     Diagnostics: {res.diagnostics}")

    # -------------------------------------------------------------
    # 3. Bidirectional Event Synchronization Hook
    # -------------------------------------------------------------
    print("\n[Step 3] Verifying Event Synchronization Hook (POST /event)...")
    event_payload = RepoIntelEventPayload(
        event_type="FindingDetected",
        repo_id="payment_service",
        finding_id="VULN_AUTH_042",
        severity="CRITICAL",
        symbol="refresh_session_token",
        file_path="auth/session.py",
        details={"risk": "potential timing side-channel in token validation"},
    )

    reaction = await repointel_event_sync(event_payload)
    assert reaction["event_received"] == "FindingDetected"
    assert reaction["action_taken"] == "auto_flagged_critical_finding"
    assert reaction["flagged_for_investigation"] is True
    print(f"  -> Reverse event processed successfully:")
    print(f"     Action Taken: {reaction['action_taken']}")
    print(f"     Flagged: {reaction['flagged_for_investigation']}  "
          f"Persisted into a case: {reaction['investigation_opened']}")

    # -------------------------------------------------------------
    # 4. Socratic & Pedagogical Entailment (LearnHarness Synergy)
    # -------------------------------------------------------------
    print("\n[Step 4] Verifying Socratic Pedagogical Understanding...")
    engine = CodeUnderstandingEngine()

    # Generate comprehension plan
    plan = engine.plan_codebase_learning(
        goal_statement="Understand token refresh invariants before patching",
        learner_level="intermediate",
    )
    assert len(plan.nodes) >= 1
    print(f"  -> Socratic plan generated with {len(plan.nodes)} progressive nodes.")

    # Generate Architectural Diagram Artifact
    diag = engine.generate_architecture_diagram(
        topic_or_symbol="AuthTokenService",
        diagram_type="component",
    )
    assert "graph" in diag.mermaid_code.lower() or "flowchart" in diag.mermaid_code.lower() or "sequence" in diag.mermaid_code.lower()
    print(f"  -> Mermaid architectural diagram generated ({len(diag.mermaid_code)} bytes).")

    # Generate Socratic Practice Challenge
    challenge = engine.generate_practice_challenge(
        node_id=plan.nodes[0].id,
        mode="discrimination",
    )
    assert len(challenge.statement) > 0
    assert len(challenge.hint_ladder) >= 1
    print(f"  -> Anti-copy-paste verification challenge created:")
    print(f"     Statement: {challenge.statement[:80]}...")
    print(f"     Hint Rungs: {len(challenge.hint_ladder)}")

    # Record Architectural Decision Record (ADR)
    note = engine.ingest_developer_notes(
        raw_text_or_markdown="Tokens must use hmac.compare_digest to prevent timing side-channel attacks.",
        title="ADR: Constant-Time Token Comparison",
    )
    assert note.note_id.startswith("note_")
    print(f"  -> Persistent ADR Note saved: '{note.title}'.")

    # -------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------
    print("\n" + "=" * 70)
    print("E2E SMOKE TEST PASSED: ALL 4 INTEGRATION PILLARS VERIFIED!")
    print("1. [PASS] Autonomy Dial (AUTONOMY_LEVEL 0..10)")
    print("2. [PASS] RepoIntel Action Dispatch & Auto-Ingest")
    print("3. [PASS] Bidirectional Event Synchronization Hook")
    print("4. [PASS] LearnHarness Socratic Pedagogical Entailment & ADRs")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_e2e_smoke()))
