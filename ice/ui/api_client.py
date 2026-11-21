"""ui/api_client.py — Thin HTTP client for the ICE API.

Every screen uses this client. No screen imports ``case_engine/`` directly
(spec §15.2, rule 19) — the UI is a pure API consumer.

BASE_URL comes from the API_BASE_URL env var and defaults to
http://localhost:8000/api/v1.
"""
import os

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)


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