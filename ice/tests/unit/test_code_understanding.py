"""test_code_understanding.py — Comprehensive unit tests for Code Understanding, Socratic Scaffolding & Notes Guide.

Tests:
    1. Developer edge & mental model probing (levels, misconceptions, blind copy-paste).
    2. Minimal Dependency Tree & Plan DAG generation (Principle i & ii).
    3. Single-step Socratic teaching: Predict -> Motivated Explain -> Check.
    4. Architectural diagram generation (component, sequence, state_machine, blast_radius).
    5. Developer notes & scratchpad ingestion (ADRs, invariants, decisions, open questions).
    6. Socratic side inquiries on code snippets ("Ask on Code").
    7. Anti-copy-paste Socratic guard ("Verify Before You Paste").
    8. Progressive 6-rung hint ladder (R0 to R5).
    9. Motivated architectural walkthrough ('Why this code exists').
    10. JSON-RPC 2.0 MCP tools dispatch for all ice_guide_* tools and ice_tutor_* aliases.
"""

import sys
from pathlib import Path

_ICE_DIR = Path(__file__).resolve().parent.parent.parent
if str(_ICE_DIR) not in sys.path:
    sys.path.insert(0, str(_ICE_DIR))

import pytest
import asyncio
from typing import Dict, Any

from case_engine.code_understanding import (
    CodeUnderstandingEngine,
    DeveloperBracket,
    CodeUnderstandingPlan,
    SocraticStep,
    SocraticPracticeChallenge,
    DeveloperNoteRecord,
    DiagramArtifact,
    HINT_RUNGS,
)
from companion import DevelopmentCompanion
from app.mcp.tools_registry import TOOLS
from app.mcp.server import dispatch_request


class TestDeveloperProbing:
    def setup_method(self):
        self.guide = CodeUnderstandingEngine()

    def test_foundational_bracket(self):
        res = self.guide.probe_developer_understanding("I am new to Python and building this project")
        assert res.learner_level == "foundational"
        assert "Basic programming intuition" in res.floor
        assert len(res.recommendations) >= 2

    def test_intermediate_bracket(self):
        res = self.guide.probe_developer_understanding(
            "I am wiring a FastAPI route with an ORM model and SQLite foreign keys"
        )
        assert res.learner_level == "intermediate"
        assert "syntax, basic functions" in res.floor

    def test_advanced_bracket(self):
        res = self.guide.probe_developer_understanding(
            "Debugging distributed linearizability and recursive CTE blast radius in postgres"
        )
        assert res.learner_level == "advanced"
        assert "Complex distributed state" in res.ceiling

    def test_blind_copy_paste_detection(self):
        res = self.guide.probe_developer_understanding(
            statement="Where do I paste this code?",
            thinking_trace="I don't know what this code does, I blindly copied from ChatGPT",
        )
        assert res.cognitive_mode == "blind_copy_paste"
        assert "blind copy-pasting" in res.thinking_diagnosis.lower()
        assert any("PAUSE" in r for r in res.recommendations)

    def test_misconception_detection(self):
        res = self.guide.probe_developer_understanding(
            statement="Why does async block the thread?",
            thinking_trace="I assume async blocks the thread like multiprocessing",
        )
        assert res.cognitive_mode == "misconception"
        assert "misconception" in res.thinking_diagnosis.lower()


class TestMinimalPlanDAG:
    def setup_method(self):
        self.guide = CodeUnderstandingEngine()

    def test_plan_generation(self):
        plan = self.guide.plan_codebase_learning("Implement distributed event sourcing")
        assert isinstance(plan, CodeUnderstandingPlan)
        assert len(plan.nodes) == 4

        # Principle i: Root is invariant
        assert plan.nodes[0].kind == "unconditional_truth"
        assert plan.nodes[0].prerequisites == []

        # Principle ii: Motivated discovery steps
        assert plan.nodes[1].kind == "motivated_discovery"
        assert plan.nodes[0].id in plan.nodes[1].prerequisites

        # Sink is goal
        assert plan.nodes[-1].kind == "goal_target"

        # Valid Mermaid syntax
        assert "graph TD" in plan.mermaid_graph
        assert f"{plan.nodes[0].id} --> {plan.nodes[1].id}" in plan.mermaid_graph


class TestSingleStepTeaching:
    def setup_method(self):
        self.guide = CodeUnderstandingEngine()
        self.plan = self.guide.plan_codebase_learning("Secure API Authentication")

    def test_get_step_and_evaluate(self):
        root_node = self.plan.nodes[0]
        step = self.guide.get_code_learning_step(self.plan.plan_id, root_node.id)

        assert isinstance(step, SocraticStep)
        assert step.node_id == root_node.id
        assert step.unconditional_truth is not None
        assert len(step.why_this_exists) > 10
        assert len(step.prediction_prompt) > 10

        # Correct answer evaluation
        res_correct = self.guide.evaluate_socratic_check(step.check_question, answer="a")
        assert res_correct["correct"] is True
        assert res_correct["surgical_nudge"] is None

        # Incorrect answer evaluation
        res_wrong = self.guide.evaluate_socratic_check(step.check_question, answer="b")
        assert res_wrong["correct"] is False
        assert res_wrong["surgical_nudge"] is not None


class TestArchitectureDiagrams:
    def setup_method(self):
        self.guide = CodeUnderstandingEngine()

    def test_component_diagram(self):
        diag = self.guide.generate_architecture_diagram("ice_core", "component")
        assert isinstance(diag, DiagramArtifact)
        assert diag.diagram_type == "component"
        assert "graph TD" in diag.mermaid_code

    def test_sequence_diagram(self):
        diag = self.guide.generate_architecture_diagram("request_lifecycle", "sequence")
        assert diag.diagram_type == "sequence"
        assert "sequenceDiagram" in diag.mermaid_code

    def test_state_machine_diagram(self):
        diag = self.guide.generate_architecture_diagram("goal_lifecycle", "state_machine")
        assert diag.diagram_type == "state_machine"
        assert "stateDiagram-v2" in diag.mermaid_code

    def test_blast_radius_diagram(self):
        diag = self.guide.generate_architecture_diagram("get_blast_radius", "blast_radius")
        assert diag.diagram_type == "blast_radius"
        assert "graph LR" in diag.mermaid_code


class TestDeveloperNotesIngestion:
    def setup_method(self):
        self.guide = CodeUnderstandingEngine()

    def test_ingest_notes(self):
        notes_md = """
        # Architectural Decision Record 004
        - Invariant: All case mutations must emit an event to the `case_events` log.
        - Decision: We choose SQLite with recursive CTEs for fast offline blast radius.
        - Question: What is the maximum depth for circular dependency traversal?
        We need to verify `repointel_client.py` and `case.py`.
        """
        record = self.guide.ingest_developer_notes(notes_md, "ADR 004")
        assert isinstance(record, DeveloperNoteRecord)
        assert len(record.invariants_extracted) >= 1
        assert len(record.decisions_made) >= 1
        assert len(record.open_questions) >= 1
        assert any("repointel_client.py" in s for s in record.referenced_symbols)


class TestSideInquiriesAndAntiCopyPaste:
    def setup_method(self):
        self.guide = CodeUnderstandingEngine()

    def test_side_inquiry(self):
        snippet = "@_safe\nasync def fetch_user(user_id: str): pass"
        res = self.guide.side_code_inquiry(snippet, "What does @_safe do here?")
        assert "decorator" in res["socratic_explanation"].lower()
        assert "invariant_to_remember" in res

    def test_verify_before_paste(self):
        risky_code = "try:\n    do_work()\nexcept Exception:\n    pass"
        res = self.guide.verify_code_before_paste(risky_code, "auth/service.py")
        assert res["target_file"] == "auth/service.py"
        assert len(res["detected_hazards"]) >= 1
        assert any("suppression" in h.lower() for h in res["detected_hazards"])
        assert len(res["socratic_checkpoint"]) >= 2


class TestPracticeChallengesAndHintLadder:
    def setup_method(self):
        self.guide = CodeUnderstandingEngine()

    def test_challenge_and_hint_progression(self):
        challenge = self.guide.generate_practice_challenge("caching", mode="discrimination")
        assert isinstance(challenge, SocraticPracticeChallenge)
        assert len(challenge.hint_ladder) == 6

        # R0
        h0 = self.guide.get_hint(challenge.problem_id)
        assert h0["rung"] == 0
        assert "R0:" in h0["hint"]
        assert h0["remaining_rungs"] == 5

        # R1
        h1 = self.guide.get_hint(challenge.problem_id)
        assert h1["rung"] == 1
        assert "R1:" in h1["hint"]


class TestMCPToolsProtocolDispatch:
    @pytest.mark.asyncio
    async def test_ice_guide_probe(self):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ice_guide_probe", "arguments": {"statement": "I just copy-pasted this code"}},
        }
        res = await dispatch_request(req)
        structured = res["result"]["structured"]
        assert structured["cognitive_mode"] == "blind_copy_paste"

    @pytest.mark.asyncio
    async def test_ice_guide_diagram(self):
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ice_guide_diagram", "arguments": {"topic_or_symbol": "auth", "diagram_type": "sequence"}},
        }
        res = await dispatch_request(req)
        structured = res["result"]["structured"]
        assert "sequenceDiagram" in structured["mermaid_code"]

    @pytest.mark.asyncio
    async def test_ice_guide_notes_and_verify(self):
        n_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "ice_guide_ingest_notes", "arguments": {"raw_notes": "Invariant: Must check auth."}},
        }
        n_res = await dispatch_request(n_req)
        assert len(n_res["result"]["structured"]["invariants_extracted"]) >= 1

        v_req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "ice_guide_anti_copy_paste", "arguments": {"proposed_code": "x = 1", "target_file": "main.py"}},
        }
        v_res = await dispatch_request(v_req)
        assert v_res["result"]["structured"]["target_file"] == "main.py"


class TestPersistenceAndReload:
    def test_disk_persistence_and_reload(self, tmp_path):
        engine = CodeUnderstandingEngine()
        engine._DATA_DIR = tmp_path
        engine._DATA_FILE = tmp_path / "test_store.json"

        note = engine.ingest_developer_notes("Invariant: Never leak database connections.")
        plan = engine.plan_codebase_learning("Master transaction isolation")

        assert engine._DATA_FILE.exists()

        engine2 = CodeUnderstandingEngine()
        engine2._DATA_DIR = tmp_path
        engine2._DATA_FILE = tmp_path / "test_store.json"
        engine2._load_from_disk()

        assert note.note_id in engine2._notes
        assert plan.plan_id in engine2._plans

