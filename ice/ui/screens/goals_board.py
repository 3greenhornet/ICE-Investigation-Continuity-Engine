"""ui/screens/goals_board.py — Project Goals, Capability Decomposition & Measurable Progress."""
import streamlit as st

from ui import api_client


def render():
    case_id = st.session_state.get("current_case_id")
    if not case_id:
        st.info("Select or create a case in Case Overview first.")
        return

    repo_scope = st.session_state.get("repointel_repo")
    st.subheader("Project Goals & Measurable Code Progress")

    with st.expander("➕ Add Goal / Constraint / Milestone", expanded=False):
        with st.form("create_goal_form", clear_on_submit=True):
            statement = st.text_input(
                "Goal Statement",
                placeholder="e.g. Improve retrieval precision on symbol and concept queries",
            )
            c1, c2, c3 = st.columns(3)
            kind = c1.selectbox(
                "Kind",
                ["goal", "need", "requirement", "quality_objective", "constraint", "milestone", "open_question"],
            )
            clarity = c2.slider("Clarity Level", min_value=1, max_value=4, value=3)
            priority = c3.slider("Priority (1 = highest)", min_value=1, max_value=5, value=2)
            success_criteria = st.text_area(
                "Success Criteria (split into verifiable checks)",
                placeholder="e.g. Rerank candidates cleanly; MRR >= 0.8 on benchmark; zero critical findings",
                height=75,
            )
            if st.form_submit_button("Create Goal"):
                if not statement.strip():
                    st.warning("Goal statement cannot be empty.")
                else:
                    try:
                        created = api_client.run_async(
                            api_client.create_goal(
                                case_id=case_id,
                                statement=statement.strip(),
                                kind=kind,
                                clarity_level=clarity,
                                priority=priority,
                                success_criteria=success_criteria.strip() or None,
                            )
                        )
                        st.success(f"Goal created ({created['goal_id']})")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to create goal: {exc}")

    col_top1, col_top2, col_top3 = st.columns(3)
    if col_top1.button("🔄 Reconcile Intent vs Code"):
        try:
            rec = api_client.run_async(api_client.get_goal_reconciliation(case_id))
            st.session_state["last_goal_reconciliation"] = rec
        except Exception as exc:
            st.error(f"Reconciliation failed: {exc}")

    if col_top2.button("✨ Propose 'Would-Be-Nice' Follow-On Goals"):
        try:
            adj = api_client.run_async(api_client.propose_adjacent_goals(case_id, repo=repo_scope))
            st.session_state["last_adjacent_proposals"] = adj
            st.success(f"Proposed {len(adj.get('proposals', []))} follow-on goals (origin=ice_proposal).")
        except Exception as exc:
            st.error(f"Adjacent goal discovery failed: {exc}")

    if col_top3.button("🧪 Draft Lab Action from Research"):
        try:
            act = api_client.run_async(api_client.trigger_lab_proposal(case_id, repo=repo_scope))
            st.success(f"Proposed REVERSIBLE Lab action `{act.get('action_id')}` (approve in Case Overview).")
        except Exception as exc:
            st.error(f"Lab proposal draft failed: {exc}")

    try:
        goals = api_client.run_async(api_client.list_goals(case_id, include_superseded=False))
    except Exception as exc:
        st.error(f"Failed to load goals: {exc}")
        return

    if not goals:
        st.info("No goals recorded for this case yet. Add one above to track code maturity & progress.")
        return

    for g in goals:
        gid = g["goal_id"]
        prio_badge = f"P{g['priority']}" if g.get("priority") else "P-"
        header = f"[{g['kind']}] ({prio_badge} · {g.get('origin', 'developer_statement')}) {g['statement'][:85]}"
        with st.expander(header, expanded=False):
            st.write(f"**ID:** `{gid}` · **Status:** {g['status']} · **Clarity:** {g['clarity_level']}/4")
            if g.get("success_criteria"):
                st.write(f"**Success Criteria:** {g['success_criteria']}")

            try:
                latest = api_client.run_async(api_client.get_goal_maturity(gid))
            except Exception:
                latest = {"maturity": "unknown"}
            st.write(f"**Current Maturity:** `{latest.get('maturity', 'no_candidate')}`")
            if latest.get("note"):
                st.caption(latest["note"])

            b1, b2 = st.columns(2)
            if b1.button("🔍 Sync Maturity (RepoIntel)", key=f"sync_{gid}"):
                try:
                    with st.spinner("Probing RepoIntel code graph, semantic search & metrics..."):
                        res = api_client.run_async(api_client.sync_goal_maturity(gid, repo=repo_scope))
                    st.session_state[f"sync_result_{gid}"] = res
                    st.rerun()
                except Exception as exc:
                    st.error(f"Sync failed: {exc}")

            if b2.button("🧩 Decompose Into Checks", key=f"dec_{gid}"):
                try:
                    dec = api_client.run_async(api_client.decompose_goal(gid))
                    st.success(f"Proposed {len(dec.get('proposed_open_questions', []))} sub-questions.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Decompose failed: {exc}")

            sync_res = st.session_state.get(f"sync_result_{gid}")
            if sync_res:
                m = sync_res.get("metrics") or {}
                deltas = sync_res.get("deltas") or {}
                st.info(
                    f"Status: **{sync_res.get('progress_status', 'baseline')}** · "
                    f"Maturity: `{sync_res.get('maturity')}` · "
                    f"Checks: **{sync_res.get('checks_passed', 0)}/{sync_res.get('checks_total', 0)}** · "
                    f"Health: {m.get('health_score', 'N/A')}"
                )
                if deltas:
                    st.caption(f"Metric deltas since previous check: {deltas}")
                for chk in sync_res.get("check_details") or []:
                    icon = "✅" if chk.get("passed") else "⚠️"
                    st.write(f"  {icon} [{chk.get('status')}] {chk.get('text')} (score {chk.get('score', 0):.2f})")

    if st.session_state.get("last_goal_reconciliation"):
        with st.expander("Intent vs Code Reconciliation Report", expanded=True):
            st.json(st.session_state["last_goal_reconciliation"])

    if st.session_state.get("last_adjacent_proposals"):
        with st.expander("Adjacent Possible / 'Would-Be-Nice' Proposals", expanded=False):
            st.json(st.session_state["last_adjacent_proposals"])
