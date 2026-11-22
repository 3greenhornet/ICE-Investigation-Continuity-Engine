"""ui/screens/case_overview.py — Case Overview screen (spec §15.3)."""
import asyncio

import streamlit as st

from ui import api_client


def render():
    with st.sidebar:
        st.header("Case Selector")
        try:
            cases = asyncio.run(api_client.list_cases())
        except Exception as exc:
            st.error(f"Failed to load cases: {exc}")
            return

        if not cases:
            st.info("No cases yet. Create one via the CLI.")
            return

        labels = {f"{c['title']} ({c['case_id']})": c["case_id"] for c in cases}
        selected_label = st.selectbox("Case", list(labels.keys()))
        selected_id = labels[selected_label]
        st.session_state["current_case_id"] = selected_id

        if st.button("Run Investigation"):
            try:
                with st.spinner("Running investigation cycle..."):
                    result = asyncio.run(api_client.run_investigation(selected_id))
                st.success(f"Decision: {result['decision']['decision_type']}")
                st.rerun()
            except Exception as exc:
                st.error(f"Investigation failed: {exc}")

    case_id = st.session_state.get("current_case_id")
    if not case_id:
        st.info("Select a case from the sidebar.")
        return

    try:
        summary = asyncio.run(api_client.get_case_summary(case_id))
    except Exception as exc:
        st.error(f"Failed to load summary: {exc}")
        return

    st.title(f"Case: {summary['title']}")
    # Part 2 §3.4: case-type badge in the header caption (DEBUG/RESEARCH/EXPLORE).
    type_badge = summary.get("case_type", "debug").upper()
    st.caption(f"{type_badge} · {summary['objective']}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Status", summary["status"])
    c2.metric("Required Confidence", f"{summary['required_confidence']:.2f}")
    c3.metric("Deadline", summary.get("decision_deadline") or "None")

    st.divider()
    st.subheader("Leading Hypothesis")
    lh = summary.get("leading_hypothesis")
    if lh:
        st.metric(
            label=lh["statement"][:60],
            value=f"{lh['confidence']:.2f}",
            delta=f"v{lh['version_number']}",
        )
    else:
        st.info("No hypotheses yet.")

    st.divider()
    # Honest label (Part 2 §8.3): this buckets information-gap priorities,
    # not thread debt — the previous heading mislabeled it.
    st.subheader("Gap Priority Distribution")
    try:
        gaps = asyncio.run(api_client.list_gaps(case_id))
    except Exception:
        gaps = []

    debt_buckets = {"Critical": 0, "High": 0, "Moderate": 0, "Low": 0}
    for gap in gaps:
        p = gap["priority"]
        if p > 0.75:
            debt_buckets["Critical"] += 1
        elif p > 0.5:
            debt_buckets["High"] += 1
        elif p > 0.25:
            debt_buckets["Moderate"] += 1
        else:
            debt_buckets["Low"] += 1
    cols = st.columns(4)
    for i, (label, count) in enumerate(debt_buckets.items()):
        cols[i].metric(label, count)

    st.divider()
    st.subheader("Thread Debt")
    try:
        threads_response = asyncio.run(api_client.get_active_threads_debt(case_id))
    except Exception:
        threads_response = []

    if not threads_response:
        st.caption("No active investigation threads.")
    else:
        for t in threads_response[:5]:
            st.write(f"**{t['debt']:.2f}** — {t['question'][:100]}")
            st.caption(
                f"strength {t['strength']:.2f} · age {t['age_days']:.1f}d · "
                f"impact {t['decision_impact']:.2f} · uncertainty {t['uncertainty']:.2f}"
            )

    st.divider()
    st.subheader("Active Contradictions")
    try:
        contradictions = asyncio.run(api_client.list_contradictions(case_id))
    except Exception:
        contradictions = []

    if not contradictions:
        st.info("No active contradictions.")
    else:
        for c in contradictions:
            with st.expander(f"{c['contradiction_id'][:12]} — severity {c['severity']:.2f}"):
                st.write(f"Claim A: {c['claim_a']}")
                st.write(f"Claim B: {c['claim_b']}")
                st.progress(c["severity"], text=f"Severity: {c['severity']:.2f}")

    st.divider()
    st.subheader("Next Recommended Action")
    na = summary.get("next_action")
    if na:
        st.info(f"**{na['action_type']}**: {na.get('target') or 'unspecified'}")
        st.caption(
            f"Priority: {na['priority']:.2f} | ID: {na['action_id']} | "
            f"Class: {na.get('approval_class', 'READ_ONLY')} | "
            f"Approval Required: {na.get('approval_required', True)} | Status: {na.get('status', 'proposed')}"
        )
        col_act1, col_act2, col_act3 = st.columns(3)
        act_id = na["action_id"]
        status = na.get("status", "proposed")

        if status == "proposed":
            if col_act1.button("✅ Approve Action", key=f"app_{act_id}"):
                try:
                    asyncio.run(api_client.approve_action(act_id))
                    st.success(f"Action {act_id} approved!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Approval failed: {exc}")
            if col_act2.button("❌ Reject Action", key=f"rej_{act_id}"):
                try:
                    asyncio.run(api_client.reject_action(act_id))
                    st.warning(f"Action {act_id} rejected.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Rejection failed: {exc}")
        elif status == "approved":
            if col_act1.button("⚡ Execute Action", key=f"exec_{act_id}"):
                try:
                    with st.spinner("Executing action via executor..."):
                        res = asyncio.run(api_client.execute_action(act_id))
                    st.success(f"Executed: {res.get('status', 'completed')}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Execution failed: {exc}")
    else:
        st.info("No pending actions.")

    st.divider()
    # Part 2 §5.3: endogenous candidates the system wants to surface —
    # aged threads, uncited evidence, stuck hypotheses, aged contradictions.
    st.subheader("The system noticed")
    try:
        suggestions = asyncio.run(api_client.get_suggestions(case_id))
    except Exception:
        suggestions = []

    if not suggestions:
        st.caption("Nothing currently needs your attention.")
    else:
        for s in suggestions[:5]:
            icon = {
                "investigation_thread": "🧵",
                "evidence_review": "📄",
                "hypothesis_revisit": "❓",
                "contradiction_revisit": "⚡",
            }.get(s["item_type"], "•")
            st.write(f"{icon} **[{s['urgency']:.2f}]** {s['content'][:200]}")