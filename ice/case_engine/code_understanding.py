"""case_engine/code_understanding.py — Software Architecture Learning, Notes Ingestion & Socratic Guidance Engine.

Owner:      ICE Developer Understanding System.
Purpose:    Empowers developers to deeply learn, study, and understand codebases,
            architecture, and developer notes instead of being passive "blind copy-paste mediators".

Key Capabilities (Customized for Software Engineering, NOT General Math):
    1. Developer Edge & Mental Model Probing:
       - Brackets understanding on this codebase/stack (foundational, intermediate, advanced).
       - Analyzes thinking traces to detect sound reasoning, misconceptions, slips,
         or blind copy-paste habits.
    2. Minimal Dependency Tree Planning:
       - Principle i (Unconditional Invariants): System rules, transactional invariants, boundary constraints.
       - Principle ii (Motivated Discovery): "Why did someone write this? What problem forced this design?"
       - Generates Mermaid DAGs (graph TD) anchored to RepoIntel AST symbols.
    3. Single-Step Socratic Code Walkthrough:
       - Paces learning one reasoning step at a time (Predict -> Motivated Explain -> Graded Check).
       - Checks focus strictly on code realities: concurrency, null handling, error boundaries, blast radius.
    4. Architectural & Dataflow Diagrams:
       - Generates clean Mermaid diagrams:
         * 'component': Module relationships & boundaries.
         * 'sequence': End-to-end request/dataflow sequence.
         * 'state_machine': Entity/status lifecycles.
         * 'blast_radius': Upstream/downstream caller trees.
    5. Developer Notes & Scratchpad Ingestion:
       - Ingests raw markdown notes, ADRs (Architecture Decision Records), and scratchpads.
       - Extracts architectural decisions, invariants, open questions, and referenced code symbols.
    6. Side Inquiries on Snippets ("Ask on Code"):
       - Answer focused questions on specific decorators, queries, or idioms without derailing the main case.
    7. Anti-Copy-Paste Socratic Guard ("Verify Before You Paste"):
       - Analyzes AI-generated code before pasting into repository files.
       - Poses targeted Socratic questions to ensure comprehension before file modification.
    8. Progressive 6-Rung Hint Ladder:
       - R0 (Attention) -> R1 (Pattern) -> R2 (Candidate Class) -> R3 (Named) -> R4 (Outlined) -> R5 (Solution).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from case_engine.repointel_client import RepoIntelClient

logger = logging.getLogger("ice.case_engine.code_understanding")

# =====================================================================
# Constants & Enums
# =====================================================================

DEVELOPER_LEVELS = ("foundational", "intermediate", "advanced")

COGNITIVE_MODES = (
    "sound_reasoning",        # Sound software intuition
    "misconception",          # Flawed mental model (e.g. async blocks the thread)
    "unwarranted_assumption", # Assuming API succeeds without handling failure branches
    "slip",                   # Minor typographical or variable name slip
    "blind_copy_paste",       # Pasting or running code without comprehension
)

DIAGRAM_TYPES = ("component", "sequence", "state_machine", "blast_radius")

HINT_RUNGS = {
    0: "R0: Attention Redirect (cost: 0)",
    1: "R1: Pattern Redescription",
    2: "R2: Candidate Class",
    3: "R3: Proposition / Pattern Named",
    4: "R4: Move Outlined",
    5: "R5: Worked Step / Solution",
}


# =====================================================================
# Data Models
# =====================================================================

class DeveloperBracket(BaseModel):
    """Assessment of developer edge and mental models."""
    learner_level: str = "intermediate"
    floor: str = Field(..., description="What the developer reliably reasons from in this codebase.")
    ceiling: str = Field(..., description="Where the developer's understanding runs out / needs scaffolding.")
    cognitive_mode: str = "sound_reasoning"
    thinking_diagnosis: str = Field(..., description="Diagnosis of stream-of-consciousness thought.")
    uncertainty_strands: List[str] = Field(default_factory=list, description="Architectural strands that need checking.")
    recommendations: List[str] = Field(default_factory=list, description="Actionable learning steps.")


class CodeUnderstandingNode(BaseModel):
    """Structural landmark in the codebase understanding tree."""
    id: str = Field(..., description="Stable lowercase slug, e.g. 'auth-invariants'.")
    title: str
    kind: str = Field(..., description="'unconditional_truth' | 'motivated_discovery' | 'goal_target'")
    rationale: str = Field(..., description="Why this node exists.")
    prerequisites: List[str] = Field(default_factory=list, description="IDs of prerequisite nodes.")
    code_anchor: Optional[str] = Field(default=None, description="Associated RepoIntel symbol or file path.")
    status: str = "not_started"


class CodeUnderstandingPlan(BaseModel):
    """Minimal Dependency Tree for mastering a feature, goal, or module."""
    plan_id: str
    goal_statement: str
    mermaid_graph: str
    nodes: List[CodeUnderstandingNode]
    current_node_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SocraticStep(BaseModel):
    """A single atomic reasoning step in code understanding."""
    plan_id: str
    node_id: str
    node_title: str
    unconditional_truth: Optional[str] = Field(default=None, description="Core architectural invariant.")
    why_this_exists: str = Field(..., description="Motivated discovery walkthrough ('Why did someone write this?').")
    prediction_prompt: str = Field(..., description="Question to answer before reading explanation.")
    explanation: str = Field(..., description="The atomic code/architecture reasoning step.")
    check_question: Dict[str, Any] = Field(..., description="Graded Socratic code question.")


class SocraticPracticeChallenge(BaseModel):
    """Active coding / architectural challenge in inactive memory."""
    problem_id: str
    node_id: str
    mode: str = "discrimination"  # recall, discrimination, composition
    statement: str
    committed_solution: str
    hint_ladder: List[str]
    current_rung: int = -1
    concluded: bool = False
    outcome: Optional[str] = None


class DeveloperNoteRecord(BaseModel):
    """Parsed developer notes, ADR, or scratchpad document."""
    note_id: str
    title: str
    invariants_extracted: List[str]
    decisions_made: List[str]
    open_questions: List[str]
    referenced_symbols: List[str]
    summary: str


class DiagramArtifact(BaseModel):
    """Generated Mermaid diagram and explanatory notes."""
    diagram_type: str
    title: str
    mermaid_code: str
    explanation: str
    referenced_symbols: List[str]


# =====================================================================
# Core Code Understanding Engine
# =====================================================================

class CodeUnderstandingEngine:
    """Dedicated Codebase Learning, Socratic Scaffolding & Notes Guide Engine.

    Engineered specifically for software development, architectural continuity,
    and anti-blind-copy-pasting.
    """

    _shared_plans: Dict[str, CodeUnderstandingPlan] = {}
    _shared_challenges: Dict[str, SocraticPracticeChallenge] = {}
    _shared_notes: Dict[str, DeveloperNoteRecord] = {}
    _shared_knowledge: Dict[str, Dict[str, Any]] = {}

    def __init__(self, repointel_client: Optional[RepoIntelClient] = None):
        self.repointel = repointel_client or RepoIntelClient()
        self._plans = CodeUnderstandingEngine._shared_plans
        self._challenges = CodeUnderstandingEngine._shared_challenges
        self._notes = CodeUnderstandingEngine._shared_notes
        self._knowledge = CodeUnderstandingEngine._shared_knowledge

    # -----------------------------------------------------------------
    # 1. Probing & Mental Model Assessment
    # -----------------------------------------------------------------

    def probe_developer_understanding(
        self,
        statement: str,
        thinking_trace: str = "",
    ) -> DeveloperBracket:
        """Find the developer's understanding edge, mental model, and copy-paste risk."""
        combined_text = f"{statement} {thinking_trace}".lower()

        # 1. Detect Cognitive Mode & Blind Copy-Paste Tendencies
        cognitive_mode = "sound_reasoning"
        diagnosis_notes = []

        blind_patterns = [
            "where do i paste", "where to paste", "what to paste",
            "just tell me what to paste", "blindly", "copy paste", "copy-paste",
            "copied from", "dont understand", "do not understand",
            "do not know what this code does", "dont know what this does",
            "magic code", "just paste", "i have no idea how this works",
        ]
        misconception_patterns = [
            ("async", "blocks the thread"),
            ("event loop", "multiprocessing"),
            ("git rebase", "deletes all commits"),
            ("lock", "slows down single thread"),
            ("foreign key", "indexes automatically"),
            ("try except", "hides all bugs forever"),
            ("database pool", "opens new connection per query"),
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
                diagnosis_notes.append("Developer displays sound conceptual software intuition.")

        # 2. Binary search edge bracketing (Foundational vs Intermediate vs Advanced)
        advanced_keywords = [
            "concurrency", "distributed", "linearizability", "ast", "blast radius",
            "idempotency", "consensus", "race condition", "memory barrier", "bloom filter",
            "recursive cte", "deadlock", "event sourcing", "two phase commit",
        ]
        intermediate_keywords = [
            "async", "decorator", "interface", "dependency injection", "orm",
            "transaction", "unit test", "middleware", "router", "schema", "rest api",
            "sqlite", "postgres", "foreign key", "indexes",
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

        # 3. Uncertainty strands
        strands = []
        if any(k in combined_text for k in ["db", "database", "sql", "orm", "transaction"]):
            strands.append("Persistence & Transaction Isolation")
        if any(k in combined_text for k in ["api", "route", "endpoint", "http", "controller"]):
            strands.append("Request Lifecycle & Error Handling")
        if any(k in combined_text for k in ["async", "task", "thread", "worker", "lock", "concurrency"]):
            strands.append("Concurrency & Task Liveness")
        if not strands:
            strands.append("Module Boundaries & Core Architecture Invariants")

        recs = [
            f"Ground explanations in {level}-level software engineering invariants.",
            "Use Motivated Discovery ('Why did someone write this?') before modifying files.",
        ]
        if cognitive_mode == "blind_copy_paste":
            recs.insert(0, "PAUSE code generation. Run a Socratic prediction check before pasting code.")

        return DeveloperBracket(
            learner_level=level,
            floor=floor,
            ceiling=ceiling,
            cognitive_mode=cognitive_mode,
            thinking_diagnosis=" ".join(diagnosis_notes),
            uncertainty_strands=strands,
            recommendations=recs,
        )

    # -----------------------------------------------------------------
    # 2. Minimal Dependency Tree Planning
    # -----------------------------------------------------------------

    def plan_codebase_learning(
        self,
        goal_statement: str,
        learner_level: str = "intermediate",
    ) -> CodeUnderstandingPlan:
        """Construct the Minimal Dependency Tree mapping from foundational truths to the goal."""
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"

        repo_symbols = []
        if self.repointel.is_available:
            words = [w for w in re.findall(r"[A-Za-z0-9_]+", goal_statement) if len(w) > 3]
            for word in words[:3]:
                found = self.repointel.search_code(word, limit=3)
                for item in found:
                    repo_symbols.append(item.get("qualified_name") or item.get("file_path"))

        slug = re.sub(r"[^a-z0-9]+", "-", goal_statement.lower())[:24].strip("-") or "core-feature"

        nodes: List[CodeUnderstandingNode] = []

        # Root: Principle i Unconditional Invariants
        n1_id = f"{slug}-invariants"
        nodes.append(CodeUnderstandingNode(
            id=n1_id,
            title="Core Architectural Invariants (Unconditional Truths)",
            kind="unconditional_truth",
            rationale="Lock in safe, always-true architectural invariants that can be accepted at face value.",
            prerequisites=[],
            code_anchor=repo_symbols[0] if repo_symbols else None,
            status="not_started",
        ))

        # Node 2: Motivated Discovery (Principle ii)
        n2_id = f"{slug}-problem-motivation"
        nodes.append(CodeUnderstandingNode(
            id=n2_id,
            title="Why This Component Must Exist (Felt Friction)",
            kind="motivated_discovery",
            rationale="Walk through the concrete problem or failure mode that forces us to introduce this structure.",
            prerequisites=[n1_id],
            code_anchor=repo_symbols[1] if len(repo_symbols) > 1 else None,
            status="not_started",
        ))

        # Node 3: Interface & Contract Derivation
        n3_id = f"{slug}-contract-design"
        nodes.append(CodeUnderstandingNode(
            id=n3_id,
            title="Interface & Dataflow Derivation",
            kind="motivated_discovery",
            rationale="Derive function signatures and boundary contracts as the obvious consequence of the need.",
            prerequisites=[n2_id],
            code_anchor=repo_symbols[2] if len(repo_symbols) > 2 else None,
            status="not_started",
        ))

        # Node 4: The Target Goal (Sink)
        n4_id = f"{slug}-goal-sink"
        nodes.append(CodeUnderstandingNode(
            id=n4_id,
            title=f"Goal Understanding: {goal_statement[:40]}...",
            kind="goal_target",
            rationale="The goal state where the implementation feels obviously true and self-evident.",
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

        plan = CodeUnderstandingPlan(
            plan_id=plan_id,
            goal_statement=goal_statement,
            mermaid_graph=mermaid_graph,
            nodes=nodes,
            current_node_id=nodes[0].id,
        )
        self._plans[plan_id] = plan
        return plan

    # -----------------------------------------------------------------
    # 3. Single-Step Socratic Code Walkthrough
    # -----------------------------------------------------------------

    def get_code_learning_step(self, plan_id: str, node_id: str) -> SocraticStep:
        """Fetch the atomic Socratic reasoning step for a node."""
        plan = self._plans.get(plan_id)
        target_node = None
        if plan:
            for n in plan.nodes:
                if n.id == node_id:
                    target_node = n
                    break

        title = target_node.title if target_node else f"Step {node_id}"

        if "invariant" in node_id.lower():
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
                    {"label": "Because Python syntax forces all functions to be async.", "value": "c"},
                ],
                "correct_answer": "a",
                "explanation": "Event emission first guarantees that crashes leave a recoverable trail of intent.",
            }
        elif "problem" in node_id.lower() or "motivation" in node_id.lower():
            truth = "Every software abstraction must solve a concrete point of friction; otherwise it is dead weight."
            why = "If we wrote all logic in one 2,000-line script, a single syntax error would stop the entire pipeline."
            predict = "If two different modules need to query code blast radius, where should that logic live?"
            explanation = (
                "Principle ii demands motivated discovery: We do not create a RepoIntelClient or CodeUnderstandingEngine "
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

        return SocraticStep(
            plan_id=plan_id,
            node_id=node_id,
            node_title=title,
            unconditional_truth=truth,
            why_this_exists=why,
            prediction_prompt=predict,
            explanation=explanation,
            check_question=check,
        )

    def evaluate_socratic_check(
        self,
        question: Dict[str, Any],
        answer: str,
        thinking_trace: str = "",
    ) -> Dict[str, Any]:
        """Evaluate a check answer and thinking trace with surgical feedback."""
        correct = question.get("correct_answer", "").strip().lower()
        submitted = answer.strip().lower()

        is_correct = (submitted == correct) or (len(submitted) == 1 and submitted == correct)
        diag = self.probe_developer_understanding(answer, thinking_trace)

        if is_correct:
            verdict = "Correct. You recognized the load-bearing invariant."
            nudge = None
        else:
            verdict = "Incorrect. The selected answer violates a foundational invariant."
            if "fast" in thinking_trace.lower() or "speed" in thinking_trace.lower():
                nudge = "Notice: The optimization here is about safety and state recovery, not execution throughput."
            else:
                nudge = "Re-read the unconditional truth established in the previous step: What guarantees safety during a crash?"

        return {
            "correct": is_correct,
            "verdict": verdict,
            "explanation": question.get("explanation", ""),
            "surgical_nudge": nudge,
            "cognitive_mode": diag.cognitive_mode,
            "thinking_diagnosis": diag.thinking_diagnosis,
        }

    # -----------------------------------------------------------------
    # 4. Architecture & Dataflow Diagram Generator
    # -----------------------------------------------------------------

    def generate_architecture_diagram(
        self,
        topic_or_symbol: str,
        diagram_type: str = "component",
    ) -> DiagramArtifact:
        """Generate interactive Mermaid diagrams for architecture, sequence, state, or blast radius."""
        diag_type = diagram_type.lower()
        if diag_type not in DIAGRAM_TYPES:
            diag_type = "component"

        referenced_symbols = []

        if diag_type == "component":
            title = f"Component Architecture Diagram: {topic_or_symbol}"
            mermaid = (
                "graph TD\n"
                "    CLI[\"Companion / CLI Interface\"] --> ENGINE[\"Case Engine / Goals\"]\n"
                "    ENGINE --> DB[(\"PostgreSQL / SQLite Event Log\")]\n"
                "    ENGINE --> REPO[\"RepoIntel Client (AST & Call Graph)\"]\n"
                "    ENGINE --> UNDERSTAND[\"Code Understanding Engine\"]\n"
                "    UNDERSTAND --> MERMAID[\"Visual Architecture Generator\"]\n"
                "    UNDERSTAND --> HINTS[\"6-Rung Hint Ladder\"]\n"
            )
            explanation = "Shows high-level module boundaries and dependencies across ICE and RepoIntel."

        elif diag_type == "sequence":
            title = f"Dataflow Sequence: {topic_or_symbol}"
            mermaid = (
                "sequenceDiagram\n"
                "    autonumber\n"
                "    actor User as Developer / Agent\n"
                "    participant Comp as Companion / MCP\n"
                "    participant Guide as Code Understanding\n"
                "    participant Repo as RepoIntel AST\n"
                "    participant DB as Event Store\n\n"
                "    User->>Comp: Request understanding / paste check\n"
                "    Comp->>Guide: probe_understanding(text, thinking)\n"
                "    Guide->>Repo: search_code(symbol) & callers\n"
                "    Repo-->>Guide: Caller tree & blast radius\n"
                "    Guide->>DB: Record proposition & plan node\n"
                "    Guide-->>Comp: Step: Predict -> Explain -> Check\n"
                "    Comp-->>User: Socratic prompt with diagrams\n"
            )
            explanation = "Traces end-to-end execution flow from user input to RepoIntel grounding and Socratic guidance."

        elif diag_type == "state_machine":
            title = f"Entity Lifecycle State Machine: {topic_or_symbol}"
            mermaid = (
                "stateDiagram-v2\n"
                "    [*] --> Exploring : Intent Clarity Level 1\n"
                "    Exploring --> Active : Defined Criteria (Level 2)\n"
                "    Active --> Implemented : Code AST Connected (Level 3)\n"
                "    Implemented --> Verified : Invariant Tests Passing (Level 4)\n"
                "    Verified --> [*] : Goal Satisfied\n"
                "    Active --> Superseded : Direction Revised by Developer\n"
                "    Superseded --> [*]\n"
            )
            explanation = "Visualizes state transitions across the 4-level Goal Clarity and Code Maturity ladders."

        else:  # blast_radius
            title = f"Blast Radius Caller Tree: {topic_or_symbol}"
            # Ground against RepoIntel if available
            callers = []
            if self.repointel.is_available:
                callers = self.repointel.get_callers(topic_or_symbol)

            caller_nodes = []
            for i, c in enumerate(callers[:4], 1):
                cname = c.get("caller_name", f"caller_{i}")
                referenced_symbols.append(cname)
                caller_nodes.append(f"    C{i}[\"{cname}\"] --> TARGET[\"{topic_or_symbol}\"]")

            if not caller_nodes:
                caller_nodes = [
                    f"    Router[\"API Router\"] --> TARGET[\"{topic_or_symbol}\"]",
                    f"    Worker[\"Background Task\"] --> TARGET[\"{topic_or_symbol}\"]",
                ]

            mermaid = (
                "graph LR\n"
                + "\n".join(caller_nodes) + "\n"
                "    TARGET --> SUB1[\"Downstream Helper 1\"]\n"
                "    TARGET --> SUB2[\"Database Connection\"]\n"
            )
            explanation = f"Illustrates upstream callers and downstream dependencies affected if `{topic_or_symbol}` is modified."

        return DiagramArtifact(
            diagram_type=diag_type,
            title=title,
            mermaid_code=mermaid,
            explanation=explanation,
            referenced_symbols=referenced_symbols,
        )

    # -----------------------------------------------------------------
    # 5. Developer Notes & Scratchpad Ingestion
    # -----------------------------------------------------------------

    def ingest_developer_notes(
        self,
        raw_text_or_markdown: str,
        title: str = "Developer Scratchpad Notes",
    ) -> DeveloperNoteRecord:
        """Parse raw developer notes, ADRs, or scratchpads into structured invariants and decisions."""
        note_id = f"note_{uuid.uuid4().hex[:8]}"

        lines = raw_text_or_markdown.splitlines()
        invariants = []
        decisions = []
        open_questions = []
        symbols = set()

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Look for code symbols `symbol_name`
            backticks = re.findall(r"`([A-Za-z0-9_\.]+)`", line_str)
            for b in backticks:
                if len(b) > 2 and not b.isdigit():
                    symbols.add(b)

            lower = line_str.lower()
            if any(k in lower for k in ["must", "always", "never", "invariant", "rule"]):
                invariants.append(line_str.lstrip("- *0123456789."))
            elif any(k in lower for k in ["decided", "decision", "choose", "chose", "using", "use"]):
                decisions.append(line_str.lstrip("- *0123456789."))
            elif "?" in line_str or any(k in lower for k in ["todo", "fixme", "question", "how to", "why"]):
                open_questions.append(line_str.lstrip("- *0123456789."))

        if not invariants:
            invariants.append("All module modifications must preserve backward-compatible signatures.")
        if not decisions:
            decisions.append(f"Recorded notes under session: {title}")
        if not open_questions:
            open_questions.append("What automated integration test will verify these changes?")

        summary = (
            f"Parsed {len(lines)} lines from '{title}'. Extracted {len(invariants)} invariants, "
            f"{len(decisions)} architectural decisions, and {len(open_questions)} open questions."
        )

        record = DeveloperNoteRecord(
            note_id=note_id,
            title=title,
            invariants_extracted=invariants[:5],
            decisions_made=decisions[:5],
            open_questions=open_questions[:5],
            referenced_symbols=list(symbols)[:10],
            summary=summary,
        )
        self._notes[note_id] = record
        return record

    # -----------------------------------------------------------------
    # 6. Side Inquiries on Code Snippets ("Ask on Code")
    # -----------------------------------------------------------------

    def side_code_inquiry(
        self,
        code_snippet: str,
        question: str,
    ) -> Dict[str, Any]:
        """Answer a focused Socratic side inquiry on a snippet without altering the main case state."""
        # Clean explanation tailored to software engineering
        answer_parts = []
        if "@" in code_snippet:
            answer_parts.append(
                "The `@` syntax indicates a Python decorator — a higher-order function that wraps and intercepts "
                "calls to the decorated function without mutating its inner source code."
            )
        if "async" in code_snippet:
            answer_parts.append(
                "The `async` keyword declares a coroutine. Unlike synchronous blocking calls, `await` yields "
                "execution back to the event loop so other concurrent tasks can make progress while I/O completes."
            )
        if "try" in code_snippet and "finally" in code_snippet:
            answer_parts.append(
                "The `finally` block guarantees execution regardless of whether an exception was raised, "
                "ensuring connection locks or temporary files are never leaked."
            )
        if not answer_parts:
            answer_parts.append(
                f"Regarding '{question}': This snippet defines an execution contract. Inputs establish assumptions, "
                f"and outputs guarantee state transitions."
            )

        return {
            "snippet_analyzed": code_snippet[:120] + ("..." if len(code_snippet) > 120 else ""),
            "question": question,
            "socratic_explanation": " ".join(answer_parts),
            "invariant_to_remember": "Never let local side inquiries distract from verifying overall system blast radius.",
        }

    # -----------------------------------------------------------------
    # 7. Anti-Copy-Paste Socratic Guard ("Verify Before You Paste")
    # -----------------------------------------------------------------

    def verify_code_before_paste(
        self,
        proposed_code: str,
        target_file: str,
    ) -> Dict[str, Any]:
        """Socratic verification of AI-generated code to stop blind copy-pasting."""
        lines = proposed_code.strip().splitlines()
        loc = len(lines)

        # Detect potential hazards
        hazards = []
        if re.search(r"except(\s+Exception)?\s*:\s*(\n\s*)?pass", proposed_code) or re.search(r"except\s*:", proposed_code):
            hazards.append("Bare `except:` or silent suppression hides critical failure modes.")
        if "global " in proposed_code:
            hazards.append("Global mutable state breaks concurrency and thread-safety.")
        if "select *" in proposed_code.lower():
            hazards.append("Unbounded `SELECT *` can cause memory spikes on large tables.")

        socratic_questions = [
            f"1. Before pasting {loc} lines into `{target_file}`: What happens if an input argument is null/empty?",
            f"2. If this function raises an unexpected exception, will upstream callers handle or log it?",
        ]

        return {
            "target_file": target_file,
            "lines_to_paste": loc,
            "detected_hazards": hazards or ["None detected (syntax looks syntactically clean)."],
            "socratic_checkpoint": socratic_questions,
            "verification_advice": (
                "DO NOT blindly paste. Read the function contract above. Confirm you can explain "
                "what each line does before saving to disk."
            ),
        }

    # -----------------------------------------------------------------
    # 8. Motivated Code Walkthrough ("Why This Code Exists")
    # -----------------------------------------------------------------

    def explain_why_this_code_exists(
        self,
        symbol_name: str,
        repo_path: str = "",
    ) -> Dict[str, Any]:
        """3Blue1Brown-style Motivated Architectural Walkthrough."""
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
    # 9. Progressive 6-Rung Hint Ladder
    # -----------------------------------------------------------------

    def generate_practice_challenge(
        self,
        node_id: str,
        mode: str = "discrimination",
    ) -> SocraticPracticeChallenge:
        """Generate active coding practice challenge with pre-computed hint ladder."""
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
            statement = "What is the defining condition of Principle i in the Code Understanding methodology?"
            solution = (
                "Principle i requires establishing unconditional truths first: architectural invariants accepted as-is "
                "with no caveats or conditions, providing safe ground before building derivations."
            )
            hints = [
                "R0: Think back to the ground layer of the dependency tree.",
                "R1: What kind of truth admits no exceptions or 'well, usually...' hedging?",
                "R2: It is about HOW the truth is held safely without risking contradiction.",
                "R3: The principle is named 'Unconditional truths first'.",
                "R4: State the rule: safe, caveat-free, face-value architectural invariants.",
                f"R5: {solution}",
            ]
        else:  # composition
            statement = (
                "Write the function signature and docstring for an async function `safe_execute_step()` "
                "that takes a `step_id`, acquires a lock, checks node prerequisites, and emits an event."
            )
            solution = (
                "async def safe_execute_step(step_id: str, lock: asyncio.Lock, plan: CodeUnderstandingPlan) -> bool:\n"
                "    '''Executes a step only if prerequisites are verified, ensuring atomic state transitions.'''"
            )
            hints = [
                "R0: Check the 4 required parameters mentioned in the prompt.",
                "R1: Remember to declare `async def` and include type hints for `lock` and `plan`.",
                "R2: How does Python annotate asynchronous locks and structured plan objects?",
                "R3: Use `asyncio.Lock` and `CodeUnderstandingPlan` as type hints.",
                "R4: Signature: `async def safe_execute_step(step_id: str, lock: asyncio.Lock, plan: CodeUnderstandingPlan) -> bool:`",
                f"R5: {solution}",
            ]

        prob = SocraticPracticeChallenge(
            problem_id=problem_id,
            node_id=node_id,
            mode=mode,
            statement=statement,
            committed_solution=solution,
            hint_ladder=hints,
            current_rung=-1,
        )
        self._challenges[problem_id] = prob
        return prob

    def get_hint(
        self,
        problem_id: str,
        requested_rung: Optional[int] = None,
        thinking_trace: str = "",
    ) -> Dict[str, Any]:
        """Serve the next minimal hint rung from the 6-rung ladder."""
        prob = self._challenges.get(problem_id)
        if not prob:
            return {"error": f"Practice challenge '{problem_id}' not found."}

        if requested_rung is not None:
            target_rung = min(max(requested_rung, 0), len(prob.hint_ladder) - 1)
        else:
            target_rung = min(prob.current_rung + 1, len(prob.hint_ladder) - 1)

        prob.current_rung = target_rung
        hint_text = prob.hint_ladder[target_rung]
        rung_label = HINT_RUNGS.get(target_rung, f"R{target_rung}")

        stuck_diag = None
        if thinking_trace:
            diag = self.probe_developer_understanding("", thinking_trace)
            stuck_diag = diag.thinking_diagnosis

        return {
            "problem_id": problem_id,
            "rung": target_rung,
            "rung_label": rung_label,
            "hint": hint_text,
            "remaining_rungs": len(prob.hint_ladder) - 1 - target_rung,
            "thinking_diagnosis": stuck_diag,
            "doctrine_reminder": (
                "Struggle with the material is how understanding locks in. "
                "Try to reason out the next move before requesting another hint!"
            ),
        }

    # -----------------------------------------------------------------
    # 10. Durable Codebase Knowledge Graph
    # -----------------------------------------------------------------

    def extract_codebase_knowledge(
        self,
        case_id: str,
        goal_statements: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Extract objective propositions and reasoning chains from goals and code."""
        statements = goal_statements or [
            "Every state transition in ICE emits an immutable event row.",
            "RepoIntel AST symbols ground high-level goal intention in verified code reality.",
            "Socratic scaffolding moves one reasoning step at a time with verified checks.",
        ]

        nodes = []
        for i, stmt in enumerate(statements, 1):
            p_id = f"prop_{uuid.uuid4().hex[:6]}"
            nodes.append({"id": p_id, "statement": stmt, "held": True})

        chains = []
        if len(nodes) >= 2:
            chain_id = f"chain_{uuid.uuid4().hex[:6]}"
            chains.append({
                "id": chain_id,
                "premises": [nodes[0]["id"], nodes[1]["id"]],
                "conclusion": "Continuous development guidance is evidence-backed and cannot drift from reality.",
                "rationale": "When state is immutable and grounded in AST realities, guidance is provably consistent.",
            })

        return {
            "case_id": case_id,
            "propositions_extracted": len(nodes),
            "reasoning_chains_derived": len(chains),
            "propositions": nodes,
            "chains": chains,
        }

    # Backward-compatibility aliases for legacy callers
    probe_learner_edge = probe_developer_understanding
    plan_minimal_tree = plan_codebase_learning
    get_teaching_step = get_code_learning_step
    evaluate_check = evaluate_socratic_check
    generate_practice_problem = generate_practice_challenge
    extract_propositions_and_chains = extract_codebase_knowledge
