"""ui/streamlit_app.py — Investigation Continuity Engine UI entry point."""
import streamlit as st

from ui.screens.case_overview import render as render_overview
from ui.screens.hypothesis_board import render as render_hypotheses
from ui.screens.evidence_graph import render as render_evidence
from ui.screens.investigation_trace import render as render_trace
from ui.screens.decision_debugger import render as render_debugger

st.set_page_config(
    page_title="Investigation Continuity Engine",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "current_case_id" not in st.session_state:
    st.session_state["current_case_id"] = None
if "selected_hypothesis" not in st.session_state:
    st.session_state["selected_hypothesis"] = None
if "selected_evidence" not in st.session_state:
    st.session_state["selected_evidence"] = None
if "trace_filter" not in st.session_state:
    st.session_state["trace_filter"] = None

tabs = st.tabs([
    "Case Overview",
    "Hypothesis Board",
    "Evidence Graph",
    "Investigation Trace",
    "Decision Debugger",
])

with tabs[0]:
    render_overview()
with tabs[1]:
    render_hypotheses()
with tabs[2]:
    render_evidence()
with tabs[3]:
    render_trace()
with tabs[4]:
    render_debugger()