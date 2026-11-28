"""ui/api_client.py — Thin HTTP client for the ICE API.

Every screen uses this client. No screen imports ``case_engine/`` directly
(spec §15.2, rule 19) — the UI is a pure API consumer.

BASE_URL comes from the API_BASE_URL env var and defaults to
http://localhost:8000/api/v1.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BASE_URL, timeout=timeout)


def run_async(coro):
    """Run an async client coroutine safely from Streamlit's synchronous callbacks."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def create_case(
    title: str,
    objective: str,
    case_type: str = "debug",
    required_confidence: float | None = None,
):
    payload: dict = {"title": title, "objective": objective, "case_type": case_type}
    if required_confidence is not None:
        payload["required_confidence"] = required_confidence
    async with _client() as client:
        r = await client.post("/cases", json=payload)
        r.raise_for_status()
        return r.json()


async def update_case_status(case_id: str, status: str, reason: str | None = None):
    async with _client() as client:
        r = await client.patch(f"/cases/{case_id}", json={"status": status, "reason": reason})
        r.raise_for_status()
        return r.json()


async def list_cases(status=None, limit=50):
    async with _client() as client:
        r = await client.get("/cases", params={"status": status, "limit": limit})
        r.raise_for_status()
        return r.json()


async def get_case(case_id: str):
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}")
        r.raise_for_status()
        return r.json()


async def get_case_summary(case_id: str):
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/summary")
        r.raise_for_status()
        return r.json()


async def list_hypotheses(case_id: str):
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/hypotheses")
        r.raise_for_status()
        return r.json()


async def get_hypothesis_graph(hypothesis_id: str):
    async with _client() as client:
        r = await client.get(f"/hypotheses/{hypothesis_id}/graph")
        r.raise_for_status()
        return r.json()


async def list_contradictions(case_id: str):
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/contradictions")
        r.raise_for_status()
        return r.json()


async def list_gaps(case_id: str):
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/gaps")
        r.raise_for_status()
        return r.json()


async def list_evidence(case_id: str):
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/evidence")
        r.raise_for_status()
        return r.json()


async def get_evidence_chain(evidence_id: str):
    async with _client() as client:
        r = await client.get(f"/evidence/{evidence_id}/chain")
        r.raise_for_status()
        return r.json()


async def list_events(case_id: str, limit: int = 100):
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/events", params={"limit": limit})
        r.raise_for_status()
        return r.json()


async def list_actions(case_id: str):
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/actions")
        r.raise_for_status()
        return r.json()


async def approve_action(action_id: str):
    """Approve a proposed action (spec Part 4 §13.6)."""
    async with _client() as client:
        r = await client.post(f"/actions/{action_id}/approve")
        r.raise_for_status()
        return r.json()


async def execute_action(action_id: str):
    """Execute an approved action through its registered executor (spec Part 4 §13.6)."""
    async with _client() as client:
        r = await client.post(f"/actions/{action_id}/execute")
        r.raise_for_status()
        return r.json()


async def reject_action(action_id: str, reason: str = "Rejected by user in UI"):
    """Reject a proposed action with reason (spec Part 4 §13.6)."""
    async with _client() as client:
        r = await client.post(f"/actions/{action_id}/reject", json={"reason": reason})
        r.raise_for_status()
        return r.json()


async def run_investigation(case_id: str):
    async with _client() as client:
        r = await client.post(f"/cases/{case_id}/investigate", json={})
        r.raise_for_status()
        return r.json()


async def run_counterfactual(case_id: str, evidence_id: str):
    async with _client() as client:
        r = await client.post(
            f"/cases/{case_id}/replay/counterfactual",
            json={"excluded_evidence_id": evidence_id},
        )
        r.raise_for_status()
        return r.json()


async def get_decision_attribution(decision_id: str):
    async with _client() as client:
        r = await client.get(f"/decisions/{decision_id}/attribution")
        r.raise_for_status()
        return r.json()


async def list_decisions(case_id: str):
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/decisions")
        r.raise_for_status()
        return r.json()


# ---- Part 2 (ICE fixes doc fix-2-b) --------------------------------------


async def get_suggestions(case_id: str):
    """Endogenous "the system noticed" candidates (doc §5.2)."""
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/suggestions")
        r.raise_for_status()
        return r.json()


async def get_active_threads_debt(case_id: str):
    """Active investigation threads with debt scores (doc §8.3)."""
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/threads/debt")
        r.raise_for_status()
        return r.json()


async def list_proposed_edges(case_id: str):
    """Pending NLI-proposed claim→hypothesis edges (doc §10.4)."""
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/proposed-edges")
        r.raise_for_status()
        return r.json()


async def accept_proposed_edge(edge_id: str):
    """Commit a proposed edge into the evidence graph (doc §10.4)."""
    async with _client() as client:
        r = await client.post(f"/proposed-edges/{edge_id}/accept")
        r.raise_for_status()
        return r.json()


async def reject_proposed_edge(edge_id: str):
    """Dismiss a proposed edge (doc §10.4)."""
    async with _client() as client:
        r = await client.post(f"/proposed-edges/{edge_id}/reject")
        r.raise_for_status()
        return r.json()


async def ingest_external_analysis(
    case_id: str,
    llm_name: str,
    prompt_used: str,
    analysis_text: str,
    conversation_context: str | None = None,
    source_uri: str | None = None,
):
    """File an external LLM analysis as evidence (doc §2.4).

    Not wired to a Streamlit form yet — kept here so future screens (and
    scripts) can push external analyses through the same thin-client path.
    """
    async with _client() as client:
        r = await client.post(
            f"/cases/{case_id}/evidence/from-llm",
            json={
                "llm_name": llm_name,
                "prompt_used": prompt_used,
                "analysis_text": analysis_text,
                "conversation_context": conversation_context,
                "source_uri": source_uri,
            },
        )
        r.raise_for_status()
        return r.json()


async def add_evidence(
    case_id: str,
    content: str,
    evidence_type: str = "OBSERVATION",
    source_type: str = "HUMAN",
    source_uri: str | None = None,
):
    async with _client() as client:
        r = await client.post(
            f"/cases/{case_id}/evidence",
            json={
                "content": content,
                "evidence_type": evidence_type,
                "source_type": source_type,
                "source_uri": source_uri,
            },
        )
        r.raise_for_status()
        return r.json()


async def run_web_research(
    case_id: str,
    query: str,
    max_results: int = 4,
    include_github: bool = True,
    ingest: bool = True,
):
    async with _client(timeout=90.0) as client:
        r = await client.post(
            f"/cases/{case_id}/evidence/web-research",
            json={
                "query": query,
                "max_results": max_results,
                "include_github": include_github,
                "ingest": ingest,
            },
        )
        r.raise_for_status()
        return r.json()


async def list_goals(case_id: str, kind: str | None = None, include_superseded: bool = False):
    params: dict = {"include_superseded": include_superseded}
    if kind:
        params["kind"] = kind
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/goals", params=params)
        r.raise_for_status()
        return r.json()


async def create_goal(
    case_id: str,
    statement: str,
    kind: str = "goal",
    clarity_level: int = 2,
    priority: int | None = None,
    success_criteria: str | None = None,
    tags: list[str] | None = None,
):
    payload: dict = {"statement": statement, "kind": kind, "clarity_level": clarity_level}
    if priority is not None:
        payload["priority"] = priority
    if success_criteria:
        payload["success_criteria"] = success_criteria
    if tags:
        payload["tags"] = tags
    async with _client() as client:
        r = await client.post(f"/cases/{case_id}/goals", json=payload)
        r.raise_for_status()
        return r.json()


async def sync_goal_maturity(goal_id: str, repo: str | None = None):
    params = {"repo": repo} if repo else {}
    async with _client(timeout=60.0) as client:
        r = await client.post(f"/goals/{goal_id}/maturity/sync", params=params)
        r.raise_for_status()
        return r.json()


async def get_goal_maturity(goal_id: str):
    async with _client() as client:
        r = await client.get(f"/goals/{goal_id}/maturity")
        r.raise_for_status()
        return r.json()


async def decompose_goal(goal_id: str):
    async with _client() as client:
        r = await client.post(f"/goals/{goal_id}/decompose")
        r.raise_for_status()
        return r.json()


async def get_goal_reconciliation(case_id: str):
    async with _client() as client:
        r = await client.get(f"/cases/{case_id}/goal-reconciliation")
        r.raise_for_status()
        return r.json()


async def propose_adjacent_goals(case_id: str, repo: str | None = None):
    params = {"repo": repo} if repo else {}
    async with _client(timeout=60.0) as client:
        r = await client.post(f"/cases/{case_id}/goals/adjacent-proposals", params=params)
        r.raise_for_status()
        return r.json()


async def run_discovery(
    problem: str,
    case_id: str | None = None,
    repo: str | None = None,
    n_fields: int = 5,
    ingest: bool = True,
    catalogue: bool = True,
):
    payload: dict = {
        "problem": problem,
        "n_fields": n_fields,
        "ingest": ingest,
        "catalogue": catalogue,
    }
    if case_id:
        payload["case_id"] = case_id
    if repo:
        payload["repo"] = repo
    async with _client(timeout=150.0) as client:
        r = await client.post("/discovery", json=payload)
        r.raise_for_status()
        return r.json()


async def get_discovery_fields():
    async with _client() as client:
        r = await client.get("/discovery/fields")
        r.raise_for_status()
        return r.json()


async def list_mechanisms(decision: str | None = None):
    params = {"decision": decision} if decision else {}
    async with _client() as client:
        r = await client.get("/mechanisms", params=params)
        r.raise_for_status()
        return r.json()


async def decide_mechanism(mechanism_id: str, decision: str, note: str = ""):
    async with _client() as client:
        r = await client.post(
            f"/discovery/mechanisms/{mechanism_id}/decision",
            json={"decision": decision, "note": note},
        )
        r.raise_for_status()
        return r.json()


async def trigger_lab_proposal(
    case_id: str,
    instruction: str | None = None,
    repo: str | None = None,
    files: list[str] | None = None,
):
    payload: dict = {"case_id": case_id, "files": files or []}
    if instruction:
        payload["instruction"] = instruction
    if repo:
        payload["repo"] = repo
    async with _client(timeout=60.0) as client:
        r = await client.post("/repointel/lab-proposals", json=payload)
        r.raise_for_status()
        return r.json()