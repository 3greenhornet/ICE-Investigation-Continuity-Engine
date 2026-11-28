"""app/routes/guide.py — HTTP surface for ICE's code-understanding guide.

Owner:      ICE × RepoIntel Integration (CHANGES.md "Cross-project
            integration hardening").
Purpose:    `case_engine.code_understanding.CodeUnderstandingEngine` was
            reachable only through the stdio MCP tools (`ice_guide_*`), while
            RepoIntel's bridge called `/api/v1/guide/*` over HTTP and always
            fell back. This router exposes the same engine over HTTP.

Plane:      Projection. The engine keeps its own JSON store
            (`data/code_understanding_store.json`); nothing here writes ICE
            Postgres tables.

Concurrency: the engine shares class-level dicts and persists them to one
            JSON file, so calls are serialised with a process-wide lock. The
            routes are plain `def`, so FastAPI runs them in its threadpool and
            the event loop is never blocked by the engine's SQLite/file I/O.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

_ENGINE_LOCK = threading.RLock()


def _engine():
    from case_engine.code_understanding import CodeUnderstandingEngine

    return CodeUnderstandingEngine()


def _dump(obj: Any) -> Any:
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------

class ImportLearnHarnessRequest(BaseModel):
    data_dir: Optional[str] = Field(None, max_length=1000)
    max_sessions: int = Field(5, ge=1, le=100)


class WalkthroughRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    case_id: str = "case_default"
    learner_level: str = Field("intermediate", max_length=40)
    diagram_type: str = Field("component", max_length=40)


class PlanRequest(BaseModel):
    goal_statement: str = Field(..., min_length=1, max_length=2000)
    learner_level: str = Field("intermediate", max_length=40)


class StepRequest(BaseModel):
    plan_id: str = Field(..., min_length=1, max_length=100)
    node_id: str = Field(..., min_length=1, max_length=200)


class ProbeRequest(BaseModel):
    statement: str = Field(..., min_length=1, max_length=5000)
    thinking_trace: str = Field("", max_length=20000)


class EvaluateRequest(BaseModel):
    question: Dict[str, Any]
    answer: str = Field(..., max_length=20000)
    thinking_trace: str = Field("", max_length=20000)


class DiagramRequest(BaseModel):
    topic_or_symbol: str = Field(..., min_length=1, max_length=500)
    diagram_type: str = Field("component", max_length=40)


class ExplainRequest(BaseModel):
    symbol_name: str = Field(..., min_length=1, max_length=500)
    repo_path: str = Field("", max_length=1000)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@router.post("/import-learnharness")
def import_learnharness(req: ImportLearnHarnessRequest):
    """Read-only import of LearnHarness sessions (default %APPDATA%/LearnHarness/data)."""
    with _ENGINE_LOCK:
        return _engine().read_external_learnharness(
            data_dir=req.data_dir, max_sessions=req.max_sessions
        )


@router.post("/walkthrough")
def walkthrough(req: WalkthroughRequest):
    """Plan + first Socratic step + architecture diagram for a topic, in one call.

    Response keeps the keys RepoIntel's offline fallback returns
    (topic, case_id, ladder_step, prompt, hints, source) and adds the full
    plan, step, and diagram.
    """
    with _ENGINE_LOCK:
        engine = _engine()
        plan = engine.plan_codebase_learning(req.topic, req.learner_level)
        first_node = plan.current_node_id or (plan.nodes[0].id if plan.nodes else None)
        step = engine.get_code_learning_step(plan.plan_id, first_node) if first_node else None
        diagram = engine.generate_architecture_diagram(req.topic, req.diagram_type)

    step_d = _dump(step) if step else {}
    hints = [n.rationale for n in plan.nodes[:3]]
    return {
        "topic": req.topic,
        "case_id": req.case_id,
        "ladder_step": step_d.get("node_title") or "R0_Attention",
        "prompt": step_d.get("prediction_prompt")
        or f"Focus attention on the core architectural invariants of '{req.topic}'.",
        "hints": hints,
        "plan": _dump(plan),
        "step": step_d or None,
        "diagram": _dump(diagram),
        "source": "ice_code_understanding",
    }


@router.post("/plan")
def plan(req: PlanRequest):
    with _ENGINE_LOCK:
        return _dump(_engine().plan_codebase_learning(req.goal_statement, req.learner_level))


@router.post("/step")
def step(req: StepRequest):
    with _ENGINE_LOCK:
        engine = _engine()
        if req.plan_id not in engine._plans:  # noqa: SLF001 — read-only membership check
            raise HTTPException(status_code=404, detail=f"plan {req.plan_id} not found")
        return _dump(engine.get_code_learning_step(req.plan_id, req.node_id))


@router.post("/probe")
def probe(req: ProbeRequest):
    with _ENGINE_LOCK:
        return _dump(_engine().probe_developer_understanding(req.statement, req.thinking_trace))


@router.post("/evaluate")
def evaluate(req: EvaluateRequest):
    with _ENGINE_LOCK:
        return _engine().evaluate_socratic_check(req.question, req.answer, req.thinking_trace)


@router.post("/diagram")
def diagram(req: DiagramRequest):
    with _ENGINE_LOCK:
        return _dump(_engine().generate_architecture_diagram(req.topic_or_symbol, req.diagram_type))


@router.post("/explain")
def explain(req: ExplainRequest):
    with _ENGINE_LOCK:
        return _engine().explain_why_this_code_exists(req.symbol_name, req.repo_path)
