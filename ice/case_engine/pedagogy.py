"""case_engine/pedagogy.py — LearnHarness-inspired Cognitive Scaffolding and Pedagogy Engine.

Owner:      ICE × LearnHarness Integration.
Purpose:    Prevents the developer from becoming a "blind copy-paste mediator"
            when using AI agents (Claude Code, Cursor, Copilot) or entering an
            unfinished/unfamiliar codebase.
            Enforces cognitive scaffolding, mental model assessment, motivated
            architectural discovery, and active practice with progressive hint ladders.

The Core Pedagogical Doctrine (adapted from LearnHarness):
    1. Two Knowledge States:
       - Current Understanding (what the developer holds as obviously true).
       - Goal Understanding (the architectural or engineering target).
    2. The Click:
       - Loose, lonely facts ("that's just how it is", or pasted AI code)
         collapse into generators (knowing WHY instead of knowing THAT).
    3. Principle i — Unconditional Truths First:
       - Establish caveated-free, always-true invariants and definitions at
         the foundation before building structure on top of them.
    4. Principle ii — Motivated Discovery ("How could I have discovered this?"):
       - 3Blue1Brown style: why does this code/architecture exist? What problem
         would arise if it didn't exist? Every move feels discovered, not decreed.
    5. Single-Step Teaching (Predict -> Explain -> Check):
       - Move one reasoning step at a time.
       - Predict before reading (activates prior schemas).
       - Explain clearly connecting to held truths.
       - Check landing with a verified check. If missed, diagnose & repair
         before building higher.
    6. Anti-Copy-Paste Practice & The 6-Rung Hint Ladder:
       - Challenge with problems in inactive memory (Recall, Discrimination, Composition).
       - When stuck, never dump the full solution immediately. Provide rungs:
         R0 (Attention redirect) -> R1 (Pattern redescription) -> R2 (Candidate class)
         -> R3 (Proposition named) -> R4 (Move outlined) -> R5 (Worked step).
    7. Durable Knowledge Graph:
       - Extract propositions and entailment chains from project goals and code.
    8. Non-Intrusive / Opt-In:
       - Engaged on-demand ("ONLY IF he ever wanted to").

Zero-cost & Offline:
    - Runs 100% deterministically offline without API keys.
    - Seamlessly leverages RepoIntel AST & Callgraphs when available.
    - Integrates with Gemini / LLM when configured.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from case_engine import _emit_event
from case_engine.repointel_client import RepoIntelClient

logger = logging.getLogger("ice.case_engine.pedagogy")

# =====================================================================
# Constants & Enums
# =====================================================================

LEARNER_LEVELS = ("foundational", "intermediate", "advanced")

COGNITIVE_MODES = (
    "sound_reasoning",        # Sound intuition / valid mental model
    "misconception",          # Flawed underlying mental model
    "unwarranted_assumption", # Taking a fragile premise on faith
    "slip",                   # Minor typographical or mechanical error
    "blind_copy_paste",       # Pasting or executing without comprehension
)

PRACTICE_MODES = (
    "recall",                 # Retrieve concept from weak cues
    "discrimination",         # Distinguish between confusable alternatives
    "composition",            # Run multi-step chain under load
)

HINT_RUNGS = {
    0: "R0: Attention Redirect (cost: 0)",
    1: "R1: Pattern Redescription",
    2: "R2: Candidate Class",
    3: "R3: Proposition Named",
    4: "R4: Move Outlined",
    5: "R5: Worked Step / Solution",
}


# =====================================================================
# Data Models
# =====================================================================

class BracketResult(BaseModel):
    """Output of the Prober assessing developer edge and mental models."""
    learner_level: str = "intermediate"
    floor: str = Field(..., description="What the developer reliably reasons from / holds as obviously true.")
    ceiling: str = Field(..., description="Where the developer's reasoning runs out / requires scaffolding.")
    cognitive_mode: str = "sound_reasoning"
    thinking_diagnosis: str = Field(..., description="Analysis of the developer's stream-of-consciousness thinking.")
    uncertainty_strands: List[str] = Field(default_factory=list, description="Specific concepts that need checking.")
    recommendations: List[str] = Field(default_factory=list, description="Pedagogical action items.")


class PlanNode(BaseModel):
    """Structural landmark in the Minimal Dependency Tree."""
    id: str = Field(..., description="Stable lowercase slug, e.g. 'core-invariants'.")
    title: str
    kind: str = Field(..., description="'unconditional_truth' | 'motivated_discovery' | 'goal_target'")
    rationale: str = Field(..., description="Why this node exists in the tree.")
    prerequisites: List[str] = Field(default_factory=list, description="IDs of nodes that must be understood first.")
    code_anchor: Optional[str] = Field(default=None, description="Associated symbol or file path from RepoIntel.")
    status: str = "not_started"  # not_started, predicting, explained, verified


class PlanDAG(BaseModel):
    """The Minimal Dependency Tree mapping from unconditional truths to the goal."""
    plan_id: str
    goal_statement: str
    mermaid_graph: str
    nodes: List[PlanNode]
    current_node_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TeachingStep(BaseModel):
    """A single reasoning step in the teaching arc."""
    plan_id: str
    node_id: str
    node_title: str
    unconditional_truth: Optional[str] = Field(default=None, description="Principle i: Invariant accepted as-is.")
    why_this_exists: str = Field(..., description="Principle ii: Motivated discovery walkthrough.")
    prediction_prompt: str = Field(..., description="Question to answer before reading the explanation.")
    explanation: str = Field(..., description="The atomic reasoning step.")
    check_question: Dict[str, Any] = Field(..., description="Graded check to verify understanding landed.")


class PracticeProblem(BaseModel):
    """An active practice challenge designed to train retrieval from inactive memory."""
    problem_id: str
    node_id: str
    mode: str = "discrimination"
    statement: str
    committed_solution: str = Field(..., description="Hidden commitment to ensure honest grading.")
    hint_ladder: List[str] = Field(..., description="Pre-computed 6 rungs of hints (R0 to R5).")
    current_rung: int = -1
    concluded: bool = False
    outcome: Optional[str] = None  # solved, solvedWithHints, surrendered


class PropositionNode(BaseModel):
    """Objective knowledge claim stored in the persistent knowledge graph."""
    id: str
    statement: str
    source_type: str = "code"  # goal, code, lesson, principle
    held: bool = True
    code_anchor: Optional[str] = None


class ReasoningChain(BaseModel):
    """Jointly-entailing reasoning chain connecting propositions."""
    id: str
    premises: List[str]
    conclusion: str
    rationale: str


# =====================================================================
# Pedagogy Engine Implementation
# =====================================================================

class PedagogyEngine:
    """Core Cognitive Scaffolding & Pedagogy Engine for ICE.

    Extracts and implements LearnHarness's pedagogical methods, adapting them
    specifically for software engineering and codebase comprehension.
    """

    _shared_plans: Dict[str, PlanDAG] = {}
    _shared_problems: Dict[str, PracticeProblem] = {}
    _shared_knowledge_nodes: Dict[str, PropositionNode] = {}
    _shared_reasoning_chains: Dict[str, ReasoningChain] = {}

    def __init__(self, repointel_client: Optional[RepoIntelClient] = None):
        self.repointel = repointel_client or RepoIntelClient()
        self._plans: Dict[str, PlanDAG] = PedagogyEngine._shared_plans
        self._problems: Dict[str, PracticeProblem] = PedagogyEngine._shared_problems
        self._knowledge_nodes: Dict[str, PropositionNode] = PedagogyEngine._shared_knowledge_nodes
        self._reasoning_chains: Dict[str, ReasoningChain] = PedagogyEngine._shared_reasoning_chains

    # -----------------------------------------------------------------
    # Phase 1: Probing & Mental Model Assessment
    # -----------------------------------------------------------------

    def probe_learner_edge(
        self,
        statement: str,
        thinking_trace: str = "",
        session_history: Optional[List[Dict[str, str]]] = None,
    ) -> BracketResult:
        """Find the developer's understanding edge (floor and ceiling).

        Analyzes both explicit statements and unfiltered 'thinking out loud'
        stream-of-consciousness traces to detect mental models, misconceptions,
        and blind copy-paste tendencies.
        """
        combined_text = f"{statement} {thinking_trace}".lower()

        # 1. Detect Cognitive Mode & Blind Copy-Paste Indicators
        cognitive_mode = "sound_reasoning"
        diagnosis_notes = []

        blind_patterns = [
            "where do i paste",
            "where to paste",
            "what to paste",
            "just tell me what to paste",
            "blindly",
            "copy paste",
            "copy-paste",
            "copied from",
            "dont understand",
            "do not understand",
            "do not know what this code does",
            "dont know what this does",
            "magic code",
            "just paste",
        ]
        misconception_patterns = [
            ("async", "blocks the thread"),
            ("event loop", "multiprocessing"),
            ("git rebase", "deletes all commits"),
            ("lock", "slows down single thread"),
            ("foreign key", "indexes automatically"),
            ("nosql", "has no schema ever"),
        ]

        if any(bp in combined_text for bp in blind_patterns):
            cognitive_mode = "blind_copy_paste"
            diagnosis_notes.append(
                "Detected blind copy-pasting pattern: developer is acting as a passive mediator "
                "without holding a structural mental model of the code."
            )
        else:
            for term, misconception in misconception_patterns:
                if term in combined_text and misconception in combined_text:
                    cognitive_mode = "misconception"
                    diagnosis_notes.append(
                        f"Detected conceptual misconception regarding '{term}': '{misconception}'."
                    )
                    break

        if not diagnosis_notes:
            if "i assume" in combined_text or "probably works" in combined_text:
                cognitive_mode = "unwarranted_assumption"
                diagnosis_notes.append("Developer is proceeding on unverified assumptions.")
            elif "syntax error" in combined_text or "typo" in combined_text:
                cognitive_mode = "slip"
                diagnosis_notes.append("Reasoning appears sound; issue is a minor mechanical slip.")
            else:
                diagnosis_notes.append("Developer displays sound conceptual intuition.")

        # 2. Binary search edge bracketing (Foundational vs Intermediate vs Advanced)
        advanced_keywords = [
            "concurrency", "distributed", "linearizability", "ast", "blast radius",
            "idempotency", "consensus", "race condition", "memory barrier", "bloom filter",
            "recursive cte", "deadlock",
        ]
        intermediate_keywords = [
            "async", "decorator", "interface", "dependency injection", "orm",
            "transaction", "unit test", "middleware", "router", "schema", "rest api",
        ]

        adv_matches = sum(1 for kw in advanced_keywords if kw in combined_text)
        int_matches = sum(1 for kw in intermediate_keywords if kw in combined_text)

        if adv_matches >= 2 or ("architecture" in combined_text and int_matches >= 2):
            level = "advanced"
            floor = "Understands language idioms, async flow, and standard modular software architectures."
            ceiling = "Complex distributed state, fine-grained concurrency invariants, and cross-repo blast radii."
        elif int_matches >= 1 or len(statement.split()) > 15:
            level = "intermediate"
            floor = "Understands syntax, basic functions, control flow, and standard library usage."
            ceiling = "System interactions, event propagation, async concurrency, and relational invariants."
        else:
            level = "foundational"
            floor = "Basic programming intuition and high-level project goals."
            ceiling = "Codebase organization, object/function boundaries, and framework conventions."

        # 3. Identify uncertainty strands (the dependencies that must be checked)
        strands = []
        if "database" in combined_text or "sql" in combined_text:
            strands.append("Persistence & Transaction Isolation")
        if "api" in combined_text or "endpoint" in combined_text:
            strands.append("Request Lifecycle & Error Handling")
        if "async" in combined_text or "background" in combined_text:
            strands.append("Concurrency & Task Liveness")
        if not strands:
            strands.append("Module Boundaries & Core Invariants")

        recs = [
            f"Establish Principle i unconditional truths appropriate for {level} level.",
            "Use Motivated Discovery (Principle ii) to explain 'Why this code exists' before modifying files.",
        ]
        if cognitive_mode == "blind_copy_paste":
            recs.insert(0, "PAUSE code generation. Run a Socratic prediction check before pasting code.")

        return BracketResult(
            learner_level=level,
            floor=floor,
            ceiling=ceiling,
            cognitive_mode=cognitive_mode,
            thinking_diagnosis=" ".join(diagnosis_notes),
            uncertainty_strands=strands,
            recommendations=recs,
        )

    # -----------------------------------------------------------------
    # Phase 2: Minimal Dependency Tree & Plan DAG
    # -----------------------------------------------------------------

    def plan_minimal_tree(
        self,
        goal_statement: str,
        learner_level: str = "intermediate",
        repo_context: Optional[Dict[str, Any]] = None,
    ) -> PlanDAG:
        """Construct the Minimal Dependency Tree from Current to Goal Understanding.

        Applies:
            - Principle i: Unconditional Truths First (roots).
            - Principle ii: Motivated Discovery (edges: 'Why this had to be this way').
            - Anchors nodes to RepoIntel AST symbols if available.
        """
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"

        # Check RepoIntel for symbols related to the goal
        repo_symbols = []
        if self.repointel.is_available:
            words = [w for w in re.findall(r"[A-Za-z0-9_]+", goal_statement) if len(w) > 3]
            for word in words[:3]:
                found = self.repointel.search_code(word, limit=3)
                for item in found:
                    repo_symbols.append(item.get("qualified_name") or item.get("file_path"))

        slug = re.sub(r"[^a-z0-9]+", "-", goal_statement.lower())[:24].strip("-") or "core-goal"

        # Construct minimal nodes based on level and goal
        nodes: List[PlanNode] = []

        # Root 1: Invariant / Unconditional Truth (Principle i)
        n1_id = f"{slug}-invariants"
        nodes.append(PlanNode(
            id=n1_id,
            title="Core Architecture Invariants (Unconditional Truths)",
            kind="unconditional_truth",
            rationale="Establish safe, caveat-free foundational truths that can be accepted as-is without hedging.",
            prerequisites=[],
            code_anchor=repo_symbols[0] if repo_symbols else None,
            status="not_started",
        ))

        # Node 2: Motivated Need / Problem Formulation (Principle ii)
        n2_id = f"{slug}-problem-motivation"
        nodes.append(PlanNode(
            id=n2_id,
            title="Why This Component Must Exist (Motivated Discovery)",
            kind="motivated_discovery",
            rationale="Walk through the core problem that forces us to introduce this structure.",
            prerequisites=[n1_id],
            code_anchor=repo_symbols[1] if len(repo_symbols) > 1 else None,
            status="not_started",
        ))

        # Node 3: Mechanism & Interface Design
        n3_id = f"{slug}-mechanism-design"
        nodes.append(PlanNode(
            id=n3_id,
            title="Interface & Data Flow Derivation",
            kind="motivated_discovery",
            rationale="Derive the function signatures and data flow step-by-step from the motivated need.",
            prerequisites=[n2_id],
            code_anchor=repo_symbols[2] if len(repo_symbols) > 2 else None,
            status="not_started",
        ))

        # Node 4: The Goal Understanding (Sink)
        n4_id = f"{slug}-goal-sink"
        nodes.append(PlanNode(
            id=n4_id,
            title=f"Goal Understanding: {goal_statement[:40]}...",
            kind="goal_target",
            rationale="The goal state where the implementation feels obviously true and generated by the structure.",
            prerequisites=[n3_id],
            code_anchor=repo_symbols[0] if repo_symbols else None,
            status="not_started",
        ))

        # Generate Mermaid DAG (graph TD)
        mermaid_lines = ["graph TD"]
        for node in nodes:
            escaped_title = node.title.replace('"', "'")
            mermaid_lines.append(f'    {node.id}["{escaped_title}"]')
        for node in nodes:
            for prereq in node.prerequisites:
                mermaid_lines.append(f"    {prereq} --> {node.id}")

        mermaid_graph = "\n".join(mermaid_lines)

        plan = PlanDAG(
            plan_id=plan_id,
            goal_statement=goal_statement,
            mermaid_graph=mermaid_graph,
            nodes=nodes,
            current_node_id=nodes[0].id,
        )
        self._plans[plan_id] = plan
        return plan

    # -----------------------------------------------------------------
    # Phase 3: Single-Step Teaching (Predict -> Explain -> Check)
    # -----------------------------------------------------------------

    def get_teaching_step(self, plan_id: str, node_id: str) -> TeachingStep:
        """Fetch the atomic teaching step for a node.

        Includes:
            - Prediction prompt (before reading).
            - Unconditional truth (Principle i).
            - Motivated discovery explanation (Principle ii).
            - Check question (verifying understanding landed).
        """
        plan = self._plans.get(plan_id)
        target_node = None
        if plan:
            for n in plan.nodes:
                if n.id == node_id:
                    target_node = n
                    break

        title = target_node.title if target_node else f"Step {node_id}"

        # Generate contextual step details
        if "invariant" in node_id.lower() or "unconditional" in node_id.lower():
            truth = "In this architecture, state mutations are ONLY valid if recorded through immutable events."
            why = "Without an append-only event log, concurrent agents or crashes leave partial state corrupted with no audit trail."
            predict = "What happens if an agent updates an in-memory dictionary directly during an unhandled exception?"
            explanation = (
                "An unconditional truth is a fact you can accept as-is: Every system state change must emit an event. "
                "Because the event log is append-only, an in-progress crash cannot erase prior truths. "
                "Nothing can contradict this base rule, so you can safely build on it."
            )
            check = {
                "question": "Why do we require events to be emitted before updating the read-model?",
                "options": [
                    {"label": "To ensure recovery and replay can reconstruct truthful state after a failure.", "value": "a"},
                    {"label": "To make the application run faster by bypassing disk writes.", "value": "b"},
                    {"label": "Because the Python runtime forces all functions to be async.", "value": "c"},
                ],
                "correct_answer": "a",
                "explanation": "Event emission first guarantees that crashes leave a recoverable trail of intent.",
            }
        elif "problem" in node_id.lower() or "motivation" in node_id.lower():
            truth = "Every abstraction must solve a concrete point of friction; otherwise it is dead weight."
            why = "If we wrote all logic in one 2,000-line script, a single syntax error would stop the entire pipeline."
            predict = "If two different modules need to query code blast radius, where should that logic live?"
            explanation = (
                "Principle ii demands motivated discovery: We do not create a RepoIntelClient or PedagogyEngine "
                "because OOP theory says so. We create it because without a dedicated client, every consumer would "
                "have to duplicate raw SQLite queries and schema handling. The module is born from that felt friction."
            )
            check = {
                "question": "What is the primary motivation for separating retrieval (RepoIntel) from continuity (ICE)?",
                "options": [
                    {"label": "Independent evolution: AST indexing can run offline while ICE tracks goals and reasoning.", "value": "a"},
                    {"label": "Because SQLite cannot be accessed by more than one thread.", "value": "b"},
                    {"label": "It is purely decorative to make the repository look larger.", "value": "c"},
                ],
                "correct_answer": "a",
                "explanation": "Decoupling indexing from continuity allows each engine to specialize without tight coupling.",
            }
        else:
            truth = "A function signature is a contract: inputs declare assumptions, outputs declare guarantees."
            why = "When contracts are implicit, developers copy-paste code blindly without knowing what inputs are required."
            predict = "Before writing the implementation, what should we commit to first?"
            explanation = (
                "Commit to the contract and the test condition before writing implementation lines. "
                "When you see why the contract exists, the code inside becomes the obvious, self-evident derivation."
            )
            check = {
                "question": "When modifying an existing function, what must you inspect first?",
                "options": [
                    {"label": "Its existing callers and the invariants they depend on.", "value": "a"},
                    {"label": "Just the last line of the function.", "value": "b"},
                    {"label": "Nothing, just rewrite it with modern syntax.", "value": "c"},
                ],
                "correct_answer": "a",
                "explanation": "Checking callers reveals the blast radius and prevents breaking existing contracts.",
            }

        return TeachingStep(
            plan_id=plan_id,
            node_id=node_id,
            node_title=title,
            unconditional_truth=truth,
            why_this_exists=why,
            prediction_prompt=predict,
            explanation=explanation,
            check_question=check,
        )

    def evaluate_check(
        self,
        question: Dict[str, Any],
        answer: str,
        thinking_trace: str = "",
    ) -> Dict[str, Any]:
        """Evaluate a learner's check answer and thinking trace.

        Enforces:
            - Honest grading against the committed correct answer.
            - Crediting load-bearing reasoning moves.
            - Surgical error localization without giving away the correction.
        """
        correct = question.get("correct_answer", "").strip().lower()
        submitted = answer.strip().lower()

        # Handle option letters or exact string matches
        is_correct = (submitted == correct) or (len(submitted) == 1 and submitted == correct)

        thinking_diag = self.probe_learner_edge(answer, thinking_trace)

        if is_correct:
            verdict = "Correct. You recognized the load-bearing invariant."
            nudge = None
        else:
            verdict = "Incorrect. The selected answer violates a foundational invariant."
            # Surgical repair nudge based on thinking
            if "fast" in thinking_trace.lower() or "speed" in thinking_trace.lower():
                nudge = "Notice: The optimization here is about safety and state recovery, not execution throughput."
            else:
                nudge = "Re-read the unconditional truth established in the previous step: What guarantees safety during a crash?"

        return {
            "correct": is_correct,
            "verdict": verdict,
            "explanation": question.get("explanation", ""),
            "surgical_nudge": nudge,
            "cognitive_mode": thinking_diag.cognitive_mode,
            "thinking_diagnosis": thinking_diag.thinking_diagnosis,
        }

    # -----------------------------------------------------------------
    # Phase 4: Anti-Copy-Paste Practice & The 6-Rung Hint Ladder
    # -----------------------------------------------------------------

    def generate_practice_problem(
        self,
        node_id: str,
        mode: str = "discrimination",
        repo_context: Optional[Dict[str, Any]] = None,
    ) -> PracticeProblem:
        """Generate a practice challenge designed to train retrieval from inactive memory.

        Pre-computes:
            - Committed model solution (hidden).
            - 6-rung hint ladder (R0 to R5) to prevent blind copy-pasting.
        """
        problem_id = f"prob_{uuid.uuid4().hex[:8]}"

        if mode == "discrimination":
            statement = (
                "You have two potential approaches to query symbol blast radius in RepoIntel:\n"
                "Approach A: Query SQLite directly with recursive CTEs in `repointel_client.py`.\n"
                "Approach B: Spawn an interactive subshell and run `git log -S` for the symbol.\n"
                "Which approach respects zero-cost offline determinism, and why does the other fail?"
            )
            solution = (
                "Approach A is correct. Direct SQLite recursive CTE executes in <2ms with zero network or process "
                "overhead, giving exact caller/callee graphs. Approach B is slow, nondeterministic, and misses "
                "static call graph edges that haven't been committed yet."
            )
            hints = [
                "R0: Look at the requirement: 'zero-cost offline determinism' and call-graph structure.",
                "R1: Consider the difference between static call graphs (AST) and Git commit history.",
                "R2: One approach runs in-process via database queries; the other depends on OS subshell spawning.",
                "R3: RepoIntel's `get_blast_radius()` uses SQLite recursive CTEs (`WITH RECURSIVE`).",
                "R4: Compare latency (<2ms SQLite vs >100ms shell) and graph completeness (AST edges vs text diffs).",
                f"R5: {solution}",
            ]
        elif mode == "recall":
            statement = "What is the defining condition of Principle i in LearnHarness teaching methodology?"
            solution = (
                "Principle i requires establishing unconditional truths first: facts accepted as-is with no caveats "
                "or conditions, providing safe ground before building derivations."
            )
            hints = [
                "R0: Think back to the ground layer of the dependency tree.",
                "R1: What kind of truth admits no exceptions or 'well, usually...' hedging?",
                "R2: It is distinct from an 'axiom' (which has no parents); it is about HOW the truth is held.",
                "R3: The principle is named 'Unconditional truths first'.",
                "R4: State the rule: safe, caveat-free, face-value acceptance.",
                f"R5: {solution}",
            ]
        else:  # composition
            statement = (
                "Write the function signature and docstring for an async function `safe_execute_step()` "
                "that takes a `step_id`, acquires a lock, checks node prerequisites, and emits an event."
            )
            solution = (
                "async def safe_execute_step(step_id: str, lock: asyncio.Lock, plan: PlanDAG) -> bool:\n"
                "    '''Executes a step only if prerequisites are verified, ensuring atomic state transitions.'''"
            )
            hints = [
                "R0: Check the 4 required parameters mentioned in the prompt.",
                "R1: Remember to declare `async def` and include type hints for `lock` and `plan`.",
                "R2: How does Python annotate asynchronous locks and structured plan objects?",
                "R3: Use `asyncio.Lock` and `PlanDAG` as type hints.",
                "R4: Signature: `async def safe_execute_step(step_id: str, lock: asyncio.Lock, plan: PlanDAG) -> bool:`",
                f"R5: {solution}",
            ]

        prob = PracticeProblem(
            problem_id=problem_id,
            node_id=node_id,
            mode=mode,
            statement=statement,
            committed_solution=solution,
            hint_ladder=hints,
            current_rung=-1,
        )
        self._problems[problem_id] = prob
        return prob

    def get_hint(
        self,
        problem_id: str,
        requested_rung: Optional[int] = None,
        thinking_trace: str = "",
    ) -> Dict[str, Any]:
        """Serve the next minimal hint rung (R0 to R5) to unblock the developer.

        Never reveals what the developer can still generate.
        """
        prob = self._problems.get(problem_id)
        if not prob:
            return {"error": f"Practice problem '{problem_id}' not found."}

        if requested_rung is not None:
            target_rung = min(max(requested_rung, 0), len(prob.hint_ladder) - 1)
        else:
            target_rung = min(prob.current_rung + 1, len(prob.hint_ladder) - 1)

        prob.current_rung = target_rung
        hint_text = prob.hint_ladder[target_rung]
        rung_label = HINT_RUNGS.get(target_rung, f"R{target_rung}")

        # Assess thinking trace if provided
        stuck_diag = None
        if thinking_trace:
            diag = self.probe_learner_edge("", thinking_trace)
            stuck_diag = diag.thinking_diagnosis

        return {
            "problem_id": problem_id,
            "rung": target_rung,
            "rung_label": rung_label,
            "hint": hint_text,
            "remaining_rungs": len(prob.hint_ladder) - 1 - target_rung,
            "thinking_diagnosis": stuck_diag,
            "doctrine_reminder": (
                "Remember: struggle with the material is how understanding locks in. "
                "Try to reason out the next move before requesting another hint!"
            ),
        }

    # -----------------------------------------------------------------
    # Phase 5: Motivated Code Walkthrough ("Why This Code Exists")
    # -----------------------------------------------------------------

    def explain_why_this_code_exists(
        self,
        symbol_name: str,
        repo_path: str = "",
    ) -> Dict[str, Any]:
        """3Blue1Brown-style Motivated Architectural Walkthrough.

        Explains code through:
            1. Core Problem: What problem forced this code to exist?
            2. The Invariant (Principle i): What unconditional truth does it preserve?
            3. Motivated Discovery (Principle ii): How could you have designed this yourself?
            4. Blast Radius: What breaks if you delete or mutate this code?
        """
        # Ground against RepoIntel if available
        repo_data = {}
        if self.repointel.is_available:
            syms = self.repointel.search_symbols(symbol_name, limit=1)
            details = syms[0] if syms else None
            callers = self.repointel.get_callers(symbol_name)
            callees = self.repointel.get_callees(symbol_name)
            blast = self.repointel.get_impact_radius(symbol_name, max_depth=2)
            blast_count = len(blast) if isinstance(blast, list) else blast.get("total_affected_symbols", 0)
            repo_data = {
                "file_path": details.file_path if details else (repo_path or "unknown"),
                "line_start": details.line_start if details else 1,
                "callers": callers,
                "callees": callees,
                "blast_radius_count": blast_count,
            }

        caller_names = [c.get("caller_name") for c in repo_data.get("callers", []) if c.get("caller_name")]
        callee_names = [c.get("callee_name") for c in repo_data.get("callees", []) if c.get("callee_name")]

        caller_summary = f"{len(caller_names)} callers ({', '.join(caller_names[:3])})" if caller_names else "0 direct callers (entry point or ungrounded)"
        callee_summary = f"{len(callee_names)} dependencies ({', '.join(callee_names[:3])})" if callee_names else "0 sub-calls"

        return {
            "symbol": symbol_name,
            "file_path": repo_data.get("file_path", repo_path or "unknown"),
            "codebase_grounding": {
                "callers": caller_summary,
                "dependencies": callee_summary,
                "blast_radius_affected_symbols": repo_data.get("blast_radius_count", 0),
            },
            "principle_i_invariant": (
                f"The invariant preserved by `{symbol_name}`: It encapsulates execution state and "
                f"guarantees that upstream callers ({caller_summary}) do not suffer corrupted or partial state."
            ),
            "principle_ii_motivated_discovery": (
                f"Why did someone write `{symbol_name}`? Imagine if this function did not exist. "
                f"Every caller would have to write custom logic for {callee_summary}. "
                f"When requirements change, you would have to fix 5 different files instead of one. "
                f"You would have arrived at this exact design to eliminate that duplication."
            ),
            "anti_copy_paste_advice": (
                f"Before editing `{symbol_name}`, ask yourself: 'If I change its return type or argument order, "
                f"how many of its {repo_data.get('blast_radius_count', 0)} dependent symbols will fail?' "
                f"Always check the callers first!"
            ),
        }

    # -----------------------------------------------------------------
    # Phase 6: Durable Knowledge Graph (Propositions & Chains)
    # -----------------------------------------------------------------

    def extract_propositions_and_chains(
        self,
        case_id: str,
        goal_statements: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Extract objective propositions and reasoning chains.

        Compounds understanding across sessions so the developer's mental
        model accumulates as a structured DAG rather than fading.
        """
        nodes: List[PropositionNode] = []
        chains: List[ReasoningChain] = []

        statements = goal_statements or [
            "Every state transition in ICE emits an immutable event row.",
            "RepoIntel AST symbols ground high-level goal intention in verified code reality.",
            "Pedagogical scaffolding moves one reasoning step at a time with verified checks.",
        ]

        for i, stmt in enumerate(statements, 1):
            p_id = f"prop_{uuid.uuid4().hex[:6]}"
            node = PropositionNode(
                id=p_id,
                statement=stmt,
                source_type="goal" if goal_statements else "lesson",
                held=True,
            )
            nodes.append(node)
            self._knowledge_nodes[p_id] = node

        if len(nodes) >= 2:
            chain_id = f"chain_{uuid.uuid4().hex[:6]}"
            chain = ReasoningChain(
                id=chain_id,
                premises=[nodes[0].id, nodes[1].id],
                conclusion="Continuous development guidance is evidence-backed and cannot drift from reality.",
                rationale="When state is immutable and grounded in AST realities, guidance is provably consistent.",
            )
            chains.append(chain)
            self._reasoning_chains[chain_id] = chain

        return {
            "case_id": case_id,
            "propositions_extracted": len(nodes),
            "reasoning_chains_derived": len(chains),
            "propositions": [n.model_dump() for n in nodes],
            "chains": [c.model_dump() for c in chains],
        }
