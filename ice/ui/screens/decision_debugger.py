"""ui/screens/decision_debugger.py — Decision Debugger screen (spec §15.7)."""
import asyncio

import streamlit as st

from ui import api_client


def render():
    case_id = st.session_state.get("current_case_id")
    if not case_id:
        st.info("Select a case first.")
        return

    try:
        decisions = asyncio.run(api_client.list_decisions(case_id))
    except Exception as exc:
        st.error(f"Failed to load decisions: {exc}")
        return

    if not decisions:
        st.info("No decisions yet.")
        return

    labels = {
        f"{d['decision_type']} @ {d['created_at'][:19]} ({d['decision_id'][:12]})": d["decision_id"]
        for d in decisions
    }
    selected_label = st.selectbox("Decision", list(labels.keys()))
    selected_id = labels[selected_label]

    try:
        attribution = asyncio.run(api_client.get_decision_attribution(selected_id))
    except Exception as exc:
        st.error(f"Failed to load attribution: {exc}")
        return

    st.subheader(f"Decision {selected_id}")
    st.write(f"**Type:** {attribution['decision_type']}")
    st.write(f"**Confidence:** {attribution['confidence']:.2f}")
    st.write(f"**Rationale:** {attribution['rationale']}")
    if attribution.get("recommended_action_id"):
        st.write(f"**Recommended Action:** {attribution['recommended_action_id']}")

    st.divider()
    st.subheader("Top Factors")
    factors = attribution.get("top_factors") or {}
    if factors:
        for k, v in factors.items():
            if isinstance(v, (int, float)):
                st.write(f"- **{k}:** {v:.3f}")
            else:
                st.write(f"- **{k}:** {v}")
    else:
        st.caption("No factors recorded.")

    st.divider()
    st.subheader("Alternatives Considered")
    alts = attribution.get("alternatives") or []
    if alts:
        for a in alts:
            st.write(
                f"- {a.get('action_type')} ({a.get('action_id', '')[:10]}): "
                f"priority {a.get('priority', 0):.3f}"
            )
    else:
        st.caption("No alternatives recorded.")