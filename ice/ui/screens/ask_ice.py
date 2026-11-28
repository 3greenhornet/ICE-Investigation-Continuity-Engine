"""ui/screens/ask_ice.py — single-entry conversational assistant tab for ICE."""
import streamlit as st

from ui import api_client


def render():
    st.subheader("Ask ICE")
    st.caption(
        "Describe a bug, observation, or question in plain English. "
        "ICE handles evidence, claims, hypotheses, code checks, and next steps."
    )

    case_id = st.session_state.get("current_case_id")
    if not case_id:
        try:
            cases = api_client.run_async(api_client.list_cases())
            if cases:
                case_id = cases[0]["case_id"]
                st.session_state["current_case_id"] = case_id
        except Exception:
            cases = []

    if not case_id:
        st.info("Select or create a case in the Case Overview tab first.")
        return

    st.caption(f"Active case: `{case_id}`")
    repo_id = st.session_state.get("repointel_repo")
    message = st.text_area(
        "What would you like ICE to investigate or explain?",
        placeholder="e.g. Latency spiked after yesterday's deploy — check if the DB pool or retriever is bottlenecked.",
        height=100,
        key="ask_ice_input",
    )
    col1, col2 = st.columns([1, 3])
    with col1:
        falsify = st.checkbox("Include falsification", value=False, key="ask_ice_falsify")
    with col2:
        send = st.button("Ask ICE", type="primary", key="ask_ice_send")

    if send:
        if not (message or "").strip():
            st.warning("Enter a message first.")
            return
        with st.spinner("Investigating..."):
            try:
                res = api_client.run_async(
                    api_client.ask_ice(
                        case_id=case_id,
                        message=message.strip(),
                        repo_id=repo_id,
                        enable_falsification=falsify,
                    )
                )
                st.session_state["last_ask_ice_result"] = res
            except Exception as exc:
                st.error(f"Ask ICE failed: {exc}")
                return

    res = st.session_state.get("last_ask_ice_result")
    if res:
        st.markdown("### Response")
        st.write(res.get("answer", ""))
        if res.get("needs_approval") and res.get("recommended_action"):
            st.warning(
                f"Consequential action recommended: {res['recommended_action'].get('target', '')}"
            )
        with st.expander("Internal reasoning details (hypotheses, claims, gaps, code)"):
            st.json(
                {
                    "mode": res.get("mode"),
                    "evidence_id": res.get("evidence_id"),
                    "claims_extracted": res.get("claims_extracted"),
                    "leading_hypothesis": res.get("leading_hypothesis"),
                    "contradictions": res.get("contradictions"),
                    "unresolved_gaps": res.get("unresolved_gaps"),
                    "recommended_action": res.get("recommended_action"),
                    "code_context": res.get("code_context"),
                }
            )
