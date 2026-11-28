"""ui/screens/hypothesis_board.py — Hypothesis Board screen (spec §15.4)."""
import streamlit as st

from ui import api_client


def render():
    case_id = st.session_state.get("current_case_id")
    if not case_id:
        st.info("Select a case first.")
        return

    try:
        hypotheses = api_client.run_async(api_client.list_hypotheses(case_id))
    except Exception as exc:
        st.error(f"Failed to load hypotheses: {exc}")
        return

    if not hypotheses:
        st.info("No hypotheses yet.")
        return

    # Sort by current confidence
    def _conf(h):
        try:
            graph = api_client.run_async(api_client.get_hypothesis_graph(h["hypothesis_id"]))
            return graph["current_confidence"]
        except Exception:
            return 0.0

    scored = [(h, _conf(h)) for h in hypotheses]
    scored.sort(key=lambda x: x[1], reverse=True)

    st.subheader("Hypotheses")
    for h, conf in scored:
        with st.expander(f"{h['statement'][:60]} — {conf:.2f}"):
            st.write(f"**ID:** {h['hypothesis_id']}")
            st.write(f"**Status:** {h['status']}")

            # Part 2 §11.3: origin badge — who put this hypothesis on the
            # board (the API carries origin/origin_detail since fix-2-b).
            origin = h.get("origin", "user")
            origin_detail = h.get("origin_detail")
            badge = {
                "user": "👤 you",
                "external_llm": f"🤖 {origin_detail or 'external LLM'}",
                "ice_generated": "🔧 ICE",
                "source_material": "📄 source",
            }.get(origin, origin)
            st.write(f"**Origin:** {badge}")

            graph = None
            try:
                graph = api_client.run_async(api_client.get_hypothesis_graph(h["hypothesis_id"]))
            except Exception as exc:
                st.warning(f"Could not load graph: {exc}")
                continue

            st.markdown("#### Version History")
            for v in graph["version_history"]:
                delta = (v["confidence"] - (v["prior_confidence"] or v["confidence"]))
                st.write(
                    f"v{v['version_number']} → **{v['confidence']:.3f}** "
                    f"({delta:+.3f}) — {v['reason']}"
                )

            st.markdown("#### Supporting Claims")
            if not graph["supporting_claims"]:
                st.caption("None")
            for c in graph["supporting_claims"]:
                st.write(f"- **{c['claim_id'][:10]}** ({c['claim_type']}): {c['statement']}")
                for ev in c["evidence"]:
                    st.caption(f" ↳ {ev['evidence_id'][:10]}: {ev['content_preview']}")

            st.markdown("#### Contradicting Claims")
            if not graph["contradicting_claims"]:
                st.caption("None")
            for c in graph["contradicting_claims"]:
                st.write(f"- **{c['claim_id'][:10]}** ({c['claim_type']}): {c['statement']}")
                for ev in c["evidence"]:
                    st.caption(f" ↳ {ev['evidence_id'][:10]}: {ev['content_preview']}")

    st.divider()
    # Part 2 §10.5: NLI-proposed claim→hypothesis edges awaiting review.
    # Accept commits a real evidence_graph_edges row; reject dismisses it.
    st.subheader("Proposed Claim-Hypothesis Edges")
    try:
        proposed = api_client.run_async(api_client.list_proposed_edges(case_id))
    except Exception:
        proposed = []

    if not proposed:
        st.caption("No pending proposed edges.")
    else:
        for p in proposed[:10]:
            st.write(
                f"**{p['relation'].upper()}** (conf {p['confidence']:.2f}): "
                f"claim {p['from_node'][:10]} → hyp {p['to_node'][:10]}"
            )
            c1, c2 = st.columns(2)
            if c1.button("Accept", key=f"accept_{p['edge_id']}"):
                api_client.run_async(api_client.accept_proposed_edge(p["edge_id"]))
                st.rerun()
            if c2.button("Reject", key=f"reject_{p['edge_id']}"):
                api_client.run_async(api_client.reject_proposed_edge(p["edge_id"]))
                st.rerun()