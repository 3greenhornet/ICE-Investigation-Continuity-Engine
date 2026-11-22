"""test_pedagogy.py — Unit tests for LearnHarness-inspired Cognitive Scaffolding in ICE.

Tests:
    1. Learner edge bracketing & cognitive mode detection (sound reasoning,
       misconceptions, slips, blind copy-paste).
    2. Minimal Dependency Tree & Plan DAG generation (Principle i & ii).
    3. Single-step teaching: Predict -> Motivated Explain -> Check.
    4. Anti-copy-paste practice engine & 6-rung progressive hint ladder.
    5. Motivated architectural walkthrough ('Why this code exists').
    6. Durable knowledge graph proposition & chain extraction.
    7. MCP tools registry & JSON-RPC 2.0 dispatch for all 8 ice_tutor_* tools.
"""

import sys
from pathlib import Path

_ICE_DIR = Path(__file__).resolve().parent.parent.parent
if str(_ICE_DIR) not in sys.path:
    sys.path.insert(0, str(_ICE_DIR))

import pytest
import asyncio
from typing import Dict, Any

from case_engine.pedagogy import (
    PedagogyEngine,
    BracketResult,
    PlanDAG,
    TeachingStep,
    PracticeProblem,
    HINT_RUNGS,
)
from companion import DevelopmentCompanion
from app.mcp.tools_registry import TOOLS
from app.mcp.server import dispatch_request


class TestLearnerEdgeBracketing:
    def setup_method(self):
        self.engine = PedagogyEngine()

    def test_foundational_bracket(self):
        result = self.engine.probe_learner_edge("I am just starting to code in Python")
        assert result.learner_level == "foundational"
        assert "Basic programming intuition" in result.floor
        assert len(result.recommendations) >= 2

    def test_intermediate_bracket(self):
        result = self.engine.probe_learner_edge(
            "I wrote an async router in FastAPI with unit tests and ORM models"
        )
        assert result.learner_level == "intermediate"
        assert "syntax, basic functions" in result.floor

    def test_advanced_bracket(self):
        result = self.engine.probe_learner_edge(
            "We are debugging distributed race conditions and linearizability in recursive CTE blast radius"
        )
        assert result.learner_level == "advanced"
        assert "Complex distributed state" in result.ceiling

    def test_blind_copy_paste_detection(self):
        result = self.engine.probe_learner_edge(
            statement="Can you just tell me where to paste this code?",
            thinking_trace="I don't understand what this code does, I blindly copied from claude",
        )
        assert result.cognitive_mode == "blind_copy_paste"
        assert "blind copy-pasting" in result.thinking_diagnosis.lower()
        assert any("PAUSE" in r for r in result.recommendations)

    def test_misconception_detection(self):
        result = self.engine.probe_learner_edge(
            statement="Why is async blocking the thread?",
            thinking_trace="I thought async blocks the thread like multiprocessing",
        )
        assert result.cognitive_mode == "misconception"
        assert "misconception" in result.thinking_diagnosis.lower()


class TestMinimalDependencyTree:
    def setup_method(self):
        self.engine = PedagogyEngine()

    def test_plan_dag_structure(self):
        plan = self.engine.plan_minimal_tree("Implement distributed transaction isolation")
        assert isinstance(plan, PlanDAG)
        assert len(plan.nodes) == 4

        # Principle i: Root node is unconditional truth
        root = plan.nodes[0]
        assert root.kind == "unconditional_truth"
        assert root.prerequisites == []

        # Principle ii: Motivated discovery steps
        node2 = plan.nodes[1]
        assert node2.kind == "motivated_discovery"
        assert root.id in node2.prerequisites

        # Goal target is the sink
        sink = plan.nodes[-1]
        assert sink.kind == "goal_target"
        assert sink.prerequisites == [plan.nodes[2].id]

        # Valid Mermaid graph
        assert "graph TD" in plan.mermaid_graph
        assert f"{root.id} --> {node2.id}" in plan.mermaid_graph


class TestSingleStepTeaching:
    def setup_method(self):
        self.engine = PedagogyEngine()
        self.plan = self.engine.plan_minimal_tree("Build robust caching layer")

    def test_step_generation(self):
        root_node = self.plan.nodes[0]
        step = self.engine.get_teaching_step(self.plan.plan_id, root_node.id)

        assert isinstance(step, TeachingStep)
        assert step.node_id == root_node.id
        assert step.unconditional_truth is not None
        assert len(step.why_this_exists) > 10
        assert len(step.prediction_prompt) > 10
        assert "question" in step.check_question
        assert "options" in step.check_question
        assert "correct_answer" in step.check_question

    def test_check_evaluation_correct(self):
        question = {
            "question": "What is the primary guarantee of an append-only event log?",
            "options": [
                {"label": "Immutable audit trail that survives crashes.", "value": "a"},
                {"label": "Unlimited write speed.", "value": "b"},
            ],
            "correct_answer": "a",
            "explanation": "Event emission first guarantees crash resilience.",
        }
        res = self.engine.evaluate_check(question, answer="a", thinking_trace="Option A preserves safety")
        assert res["correct"] is True
        assert "Correct" in res["verdict"]
        assert res["surgical_nudge"] is None

    def test_check_evaluation_incorrect_with_surgical_nudge(self):
        question = {
            "question": "What is the primary guarantee of an append-only event log?",
            "options": [
                {"label": "Immutable audit trail that survives crashes.", "value": "a"},
                {"label": "Unlimited write speed.", "value": "b"},
            ],
            "correct_answer": "a",
            "explanation": "Event emission first guarantees crash resilience.",
        }
        res = self.engine.evaluate_check(question, answer="b", thinking_trace="I want maximum speed")
        assert res["correct"] is False
        assert "Incorrect" in res["verdict"]
        assert res["surgical_nudge"] is not None
        assert "safety" in res["surgical_nudge"].lower()


class TestPracticeEngineAndHintLadder:
    def setup_method(self):
        self.engine = PedagogyEngine()

    def test_generate_discrimination_problem(self):
        prob = self.engine.generate_practice_problem("invariants", mode="discrimination")
        assert isinstance(prob, PracticeProblem)
        assert prob.mode == "discrimination"
        assert len(prob.hint_ladder) == 6  # R0 through R5
        assert len(prob.committed_solution) > 20

    def test_progressive_hint_ladder(self):
        prob = self.engine.generate_practice_problem("invariants", mode="discrimination")
        prob_id = prob.problem_id

        # R0: Attention redirect
        h0 = self.engine.get_hint(prob_id)
        assert h0["rung"] == 0
        assert "R0:" in h0["hint"]
        assert h0["remaining_rungs"] == 5

        # R1: Pattern redescription
        h1 = self.engine.get_hint(prob_id)
        assert h1["rung"] == 1
        assert "R1:" in h1["hint"]

        # Jump directly to R5
        h5 = self.engine.get_hint(prob_id, requested_rung=5)
        assert h5["rung"] == 5
        assert "R5:" in h5["hint"]
        assert h5["remaining_rungs"] == 0


class TestMotivatedCodeWalkthrough:
    def setup_method(self):
        self.engine = PedagogyEngine()

    def test_explain_why_code_exists(self):
        why = self.engine.explain_why_this_code_exists("get_blast_radius")
        assert why["symbol"] == "get_blast_radius"
        assert "principle_i_invariant" in why
        assert "principle_ii_motivated_discovery" in why
        assert "anti_copy_paste_advice" in why
        assert "codebase_grounding" in why


class TestKnowledgeGraphExtraction:
    def setup_method(self):
        self.engine = PedagogyEngine()

    def test_extract_propositions_and_chains(self):
        res = self.engine.extract_propositions_and_chains(
            case_id="case_test",
            goal_statements=[
                "Event logs must be append-only.",
                "RepoIntel AST symbols ground goals.",
            ],
        )
        assert res["case_id"] == "case_test"
        assert res["propositions_extracted"] == 2
        assert res["reasoning_chains_derived"] == 1
        assert len(res["chains"][0]["premises"]) == 2


class TestMCPToolsDispatch:
    @pytest.mark.asyncio
    async def test_ice_tutor_probe_mcp(self):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ice_tutor_probe", "arguments": {"statement": "I don't know where to paste code"}},
        }
        res = await dispatch_request(req)
        assert "result" in res
        structured = res["result"]["structured"]
        assert "learner_level" in structured
        assert structured["cognitive_mode"] == "blind_copy_paste"

    @pytest.mark.asyncio
    async def test_ice_tutor_plan_map_mcp(self):
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ice_tutor_plan_map", "arguments": {"goal_statement": "Build event sourcing"}},
        }
        res = await dispatch_request(req)
        structured = res["result"]["structured"]
        assert "plan_id" in structured
        assert "mermaid_graph" in structured
        assert len(structured["nodes"]) == 4

    @pytest.mark.asyncio
    async def test_ice_tutor_teach_step_mcp(self):
        plan_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "ice_tutor_plan_map", "arguments": {"goal_statement": "Secure API"}},
        }
        plan_res = await dispatch_request(plan_req)
        structured_plan = plan_res["result"]["structured"]
        plan_id = structured_plan["plan_id"]
        node_id = structured_plan["nodes"][0]["id"]

        step_req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "ice_tutor_teach_step", "arguments": {"plan_id": plan_id, "node_id": node_id}},
        }
        step_res = await dispatch_request(step_req)
        step = step_res["result"]["structured"]
        assert "prediction_prompt" in step
        assert "check_question" in step

    @pytest.mark.asyncio
    async def test_ice_tutor_evaluate_check_mcp(self):
        req = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "ice_tutor_evaluate_check",
                "arguments": {
                    "question": {"question": "Q", "correct_answer": "a", "explanation": "Exp"},
                    "answer": "a",
                    "thinking_trace": "Because invariant holds",
                },
            },
        }
        res = await dispatch_request(req)
        structured = res["result"]["structured"]
        assert structured["correct"] is True

    @pytest.mark.asyncio
    async def test_ice_tutor_practice_and_hint_mcp(self):
        prob_req = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "ice_tutor_practice_problem", "arguments": {"node_id": "test_node", "mode": "recall"}},
        }
        prob_res = await dispatch_request(prob_req)
        prob = prob_res["result"]["structured"]
        prob_id = prob["problem_id"]

        hint_req = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "ice_tutor_hint", "arguments": {"problem_id": prob_id}},
        }
        hint_res = await dispatch_request(hint_req)
        hint = hint_res["result"]["structured"]
        assert hint["rung"] == 0
        assert "R0:" in hint["hint"]

    @pytest.mark.asyncio
    async def test_ice_tutor_explain_why_mcp(self):
        req = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "ice_tutor_explain_why", "arguments": {"symbol_name": "repointel_search_code"}},
        }
        res = await dispatch_request(req)
        why = res["result"]["structured"]
        assert why["symbol"] == "repointel_search_code"
        assert "principle_ii_motivated_discovery" in why

    @pytest.mark.asyncio
    async def test_ice_tutor_extract_knowledge_mcp(self):
        req = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "ice_tutor_extract_knowledge", "arguments": {"case_id": "case_123"}},
        }
        res = await dispatch_request(req)
        kg = res["result"]["structured"]
        assert kg["case_id"] == "case_123"
        assert kg["propositions_extracted"] >= 1
