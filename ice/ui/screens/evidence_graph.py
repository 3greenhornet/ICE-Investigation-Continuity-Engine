"""ui/screens/evidence_graph.py — Evidence Graph & Filtered Research screen (spec §15.5)."""
import streamlit as st

from ui import api_client


def render():
    case_id = st.session_state.get("current_case_id")
    if not case_id:
        st.info("Select or create a case in Case Overview first.")
        return

    c_left, c_right = st.columns(2)
    with c_left.expander("🔎 Web & GitHub Research (Noise-Filtered)", expanded=False):
        with st.form("web_research_form"):
            query = st.text_input("Research Query", placeholder="e.g. FlashRank cross-encoder latency benchmark python")
            max_res = st.slider("Max Results", min_value=1, max_value=8, value=4)
            inc_gh = st.checkbox("Include GitHub repos", value=True)
            do_ingest = st.checkbox("Ingest relevant items as evidence", value=True)
            if st.form_submit_button("Run Filtered Research"):
                if len(query.strip()) < 2:
                    st.warning("Enter a search query.")
                else:
                    try:
                        with st.spinner("Searching & filtering low-signal noise..."):
                            res = api_client.run_async(
                                api_client.run_web_research(
                                    case_id=case_id,
                                    query=query.strip(),
                                    max_results=max_res,
                                    include_github=inc_gh,
                                    ingest=do_ingest,
                                )
                            )
                        st.session_state["last_web_research"] = res
                        st.success(
                            f"Kept {res.get('items', len(res.get('results', [])))} items, "
                            f"filtered out {len(res.get('filtered_out') or [])} low-signal results."
                        )
                    except Exception as exc:
                        st.error(f"Web research failed: {exc}")

    with c_right.expander("📝 Add Direct Observation / External LLM Analysis", expanded=False):
        mode = st.radio("Input Source", ["Direct Observation", "External LLM Analysis"], horizontal=True)
        if mode == "Direct Observation":
            with st.form("add_obs_form", clear_on_submit=True):
                obs_text = st.text_area("Observation Content", height=90)
                src_uri = st.text_input("Source URI (optional)")
                if st.form_submit_button("Ingest Evidence"):
                    if obs_text.strip():
                        try:
                            api_client.run_async(
                                api_client.add_evidence(
                                    case_id=case_id,
                                    content=obs_text.strip(),
                                    source_uri=src_uri.strip() or None,
                                )
                            )
                            st.success("Observation ingested.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Ingest failed: {exc}")
        else:
            with st.form("ext_llm_form", clear_on_submit=True):
                llm_name = st.text_input("Model Name", placeholder="e.g. claude-opus / deepseek-r1")
                prompt_used = st.text_input("Prompt / Question Asked")
                analysis_text = st.text_area("Analysis Output", height=90)
                if st.form_submit_button("Ingest External Analysis"):
                    if llm_name.strip() and prompt_used.strip() and analysis_text.strip():
                        try:
                            api_client.run_async(
                                api_client.ingest_external_analysis(
                                    case_id=case_id,
                                    llm_name=llm_name.strip(),
                                    prompt_used=prompt_used.strip(),
                                    analysis_text=analysis_text.strip(),
                                )
                            )
                            st.success("External LLM analysis ingested.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"External LLM ingest failed: {exc}")

    if st.session_state.get("last_web_research"):
        wr = st.session_state["last_web_research"]
        with st.expander("Last Web Research Breakdown", expanded=False):
            for r in wr.get("results") or []:
                st.write(f"- [{r.get('relevance_score', 0):.2f}] **{r.get('title')}** — {r.get('url')}")
            if wr.get("filtered_out"):
                st.caption(f"Filtered noise ({len(wr['filtered_out'])}): " + ", ".join(
                    f"{x.get('url', '')} ({x.get('filter_reason', 'low')})" for x in wr["filtered_out"][:5]
                ))

    try:
        evidence = api_client.run_async(api_client.list_evidence(case_id))
    except Exception as exc:
        st.error(f"Failed to load evidence: {exc}")
        return

    if not evidence:
        st.info("No evidence yet. Run filtered research or add an observation above.")
        return

    st.subheader("Evidence")
    for ev in evidence:
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
        chain_data = api_client.run_async(api_client.get_evidence_chain(selected))
    except Exception as exc:
        st.error(f"Failed to load chain: {exc}")
        return

    for entry in chain_data["chain"]:
        st.write(f"**{entry['node_type']}** → {entry['node_id']}")
        if entry["metadata"]:
            st.json(entry["metadata"])
        st.markdown("↓")