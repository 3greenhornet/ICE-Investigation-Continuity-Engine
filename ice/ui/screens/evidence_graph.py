"""ui/screens/evidence_graph.py — Evidence Graph screen (spec §15.5)."""
import asyncio

import streamlit as st

from ui import api_client


def render():
    case_id = st.session_state.get("current_case_id")
    if not case_id:
        st.info("Select a case first.")
        return

    try:
        evidence = asyncio.run(api_client.list_evidence(case_id))
    except Exception as exc:
        st.error(f"Failed to load evidence: {exc}")
        return

    if not evidence:
        st.info("No evidence yet.")
        return

    st.subheader("Evidence")
    for ev in evidence:
        # Part 2 §2.6/§11.4: badge MODEL_INFERENCE rows that came from an
        # external LLM (ingest_external_analysis sets source_llm).
        llm_badge = ""
        if ev.get("evidence_type") == "MODEL_INFERENCE" and ev.get("source_llm"):
            llm_badge = f" [🤖 {ev['source_llm']}]"
        label = (
            f"{ev['evidence_id'][:12]} ({ev['evidence_type']}){llm_badge}"
            f" — {ev['content'][:60]}"
        )
        if st.button(label, key=f"ev_{ev['evidence_id']}"):
            st.session_state["selected_evidence"] = ev["evidence_id"]

    selected = st.session_state.get("selected_evidence")
    if not selected:
        return

    st.divider()
    st.subheader(f"Provenance chain: {selected}")

    try:
        chain_data = asyncio.run(api_client.get_evidence_chain(selected))
    except Exception as exc:
        st.error(f"Failed to load chain: {exc}")
        return

    for entry in chain_data["chain"]:
        st.write(f"**{entry['node_type']}** → {entry['node_id']}")
        if entry["metadata"]:
            st.json(entry["metadata"])
        st.markdown("↓")