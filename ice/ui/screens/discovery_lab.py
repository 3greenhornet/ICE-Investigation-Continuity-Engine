"""ui/screens/discovery_lab.py — Cross-Domain Mechanism Discovery & Field Bandit Lab."""
import streamlit as st

from ui import api_client


def _render_decision_buttons(mech_id: str, key_prefix: str):
    c1, c2, c3, c4 = st.columns(4)
    for col, decision, label in (
        (c1, "reuse", "✅ Reuse"),
        (c2, "adapt", "🔧 Adapt"),
        (c3, "reject", "❌ Reject"),
        (c4, "defer", "⏸️ Defer"),
    ):
        if col.button(label, key=f"{key_prefix}_{mech_id}_{decision}"):
            try:
                res = api_client.run_async(api_client.decide_mechanism(mech_id, decision))
                st.success(f"Recorded '{decision}' for {mech_id} (field posterior updated).")
                if res.get("field_posterior"):
                    st.caption(res["field_posterior"])
                st.rerun()
            except Exception as exc:
                st.error(f"Decision failed: {exc}")


def render():
    st.subheader("Cross-Domain Mechanism Discovery Lab")
    st.caption(
        "Restate a problem in domain-neutral terms, sample diverse scientific fields via "
        "OpenAlex/Semantic Scholar + Thompson × DPP, inspect candidate mechanisms, and train the field bandit."
    )

    case_id = st.session_state.get("current_case_id")
    repo_scope = st.session_state.get("repointel_repo")

    with st.form("discovery_form"):
        problem = st.text_area(
            "Problem Description",
            placeholder="e.g. Recommendations keep repeating the same safe answers instead of exploring diverse useful alternatives",
            height=90,
        )
        c1, c2, c3 = st.columns(3)
        n_fields = c1.slider("Diverse Fields (DPP)", min_value=2, max_value=8, value=5)
        link_case = c2.checkbox("Attach to current case", value=bool(case_id))
        ingest = c3.checkbox("Save cards as evidence", value=bool(case_id))
        if st.form_submit_button("🔎 Discover Cross-Domain Mechanisms"):
            if len(problem.strip()) < 8:
                st.warning("Describe the problem in at least 8 characters.")
            else:
                try:
                    with st.spinner("Abstracting problem & probing diverse scientific fields..."):
                        report = api_client.run_async(
                            api_client.run_discovery(
                                problem=problem.strip(),
                                case_id=case_id if link_case else None,
                                repo=repo_scope,
                                n_fields=n_fields,
                                ingest=ingest and bool(link_case and case_id),
                                catalogue=True,
                            )
                        )
                    st.session_state["last_discovery_report"] = report
                except Exception as exc:
                    st.error(f"Discovery failed: {exc}")

    report = st.session_state.get("last_discovery_report")
    if report:
        ab = report.get("abstract") or {}
        if ab.get("neutral_statement"):
            st.info(f"**Domain-Neutral Restatement:** {ab['neutral_statement']}")
        if ab.get("search_phrases"):
            st.caption(f"Search phrases: {', '.join(ab['search_phrases'])}")
        picked = report.get("fields_selected") or []
        if picked:
            st.write("**Selected Diverse Fields:** " + ", ".join(f"{f['field']} ({'+'.join(f.get('origin', []))})" for f in picked))

        cards = report.get("cards") or []
        if cards:
            st.subheader(f"Discovered Mechanism Cards ({len(cards)})")
            for idx, c in enumerate(cards):
                with st.expander(f"[{c.get('field')}] {c.get('name')}", expanded=True):
                    st.write(f"**Original Problem:** {c.get('original_problem')}")
                    st.write(f"**Mechanism:** {c.get('mechanism')}")
                    st.write(f"**Transferable Property:** {c.get('transferable')}")
                    st.write(f"**Proposed Adaptation ({c.get('adaptation_status')}):** {c.get('adaptation')}")
                    if c.get("in_repo"):
                        st.caption(f"Already in repo: {c['in_repo']}")
                    for imp in c.get("implementations") or []:
                        st.write(f"- GitHub: [{imp.get('title')}]({imp.get('url')}) (★{imp.get('stars')})")
                    for src in c.get("sources") or []:
                        st.caption(f"Source: {src.get('title')} ({src.get('year')}) — {src.get('url')}")
                    if c.get("mechanism_id"):
                        _render_decision_buttons(c["mechanism_id"], f"card_{idx}")
        elif report.get("leads"):
            st.warning("No LLM cards extracted; showing literature leads:")
            st.json(report["leads"])

    st.divider()
    st.subheader("Mechanism Lab Catalogue & Field Bandit")
    filter_dec = st.selectbox("Filter Mechanisms by Decision", ["all", "pending", "reuse", "adapt", "reject", "defer"])
    try:
        mechs = api_client.run_async(api_client.list_mechanisms(None if filter_dec == "all" else filter_dec))
    except Exception as exc:
        st.error(f"Could not load mechanism catalogue: {exc}")
        mechs = []

    for m in mechs[:20]:
        mid = m["mechanism_id"]
        with st.expander(f"[{m.get('decision', 'pending').upper()}] {m.get('name')} — from {m.get('original_domain')}"):
            st.write(f"**Original Problem:** {m.get('original_problem')}")
            st.write(f"**Borrowed Principle:** {m.get('borrowed_principle')}")
            st.write(f"**Inverted / Target Use:** {m.get('inverted_use')}")
            if m.get("source_ref"):
                st.caption(f"Sources: {m['source_ref']}")
            _render_decision_buttons(mid, "cat")

    try:
        fields_stats = api_client.run_async(api_client.get_discovery_fields()).get("fields", [])
    except Exception:
        fields_stats = []
    if fields_stats:
        with st.expander("Learned Field Bandit Posteriors (Thompson Sampling)", expanded=False):
            for f in fields_stats[:15]:
                st.write(
                    f"- **{f['field']}**: posterior mean `{f.get('posterior_mean')}` "
                    f"(useful={f.get('useful', 0)}, reject={f.get('not_useful', 0)}, proposed={f.get('proposed', 0)})"
                )
