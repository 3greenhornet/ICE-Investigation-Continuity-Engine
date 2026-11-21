"""ui/screens/investigation_trace.py — Investigation Trace screen (spec §15.6)."""
import asyncio
from collections import Counter

import streamlit as st

from ui import api_client


EVENT_FILTERS = [
    "All",
    "evidence_ingested",
    "claim_extracted",
    "hypothesis_created",
    "hypothesis_version_created",
    "contradiction_detected",
    "decision_made",
]


def render():
    case_id = st.session_state.get("current_case_id")
    if not case_id:
        st.info("Select a case first.")
        return

    filter_choice = st.selectbox("Event type", EVENT_FILTERS)
    st.session_state["trace_filter"] = filter_choice
    try:
        events = asyncio.run(api_client.list_events(case_id, limit=200))
    except Exception as exc:
        st.error(f"Failed to load events: {exc}")
        return

    if filter_choice != "All":
        events = [e for e in events if e["event_type"] == filter_choice]

    st.caption(f"{len(events)} events")

    for ev in events:
        st.write(
            f"**{ev['wall_clock_time'][:19]}** — "
            f"`{ev['event_type']}` — seq {ev['event_seq']}"
        )
        payload = ev.get("payload") or {}
        preview = {k: v for k, v in payload.items() if k in (
            "evidence_id", "claim_count", "hypothesis_id", "prior_confidence",
            "new_confidence", "reason", "contradiction_id", "decision_type",
        )}
        if preview:
            st.caption(preview)
        st.markdown("---")