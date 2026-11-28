"""ui/streamlit_app.py — Investigation Continuity Engine UI entry point."""
import streamlit as st

from ui.screens.ask_ice import render as render_ask
from ui.screens.case_overview import render as render_overview
from ui.screens.goals_board import render as render_goals
from ui.screens.hypothesis_board import render as render_hypotheses
from ui.screens.evidence_graph import render as render_evidence
from ui.screens.discovery_lab import render as render_discovery
from ui.screens.investigation_trace import render as render_trace
from ui.screens.decision_debugger import render as render_debugger

st.set_page_config(
    page_title="Investigation Continuity Engine",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

for _k, _default in (
    ("current_case_id", None),
    ("repointel_repo", None),
    ("selected_hypothesis", None),
    ("selected_evidence", None),
    ("trace_filter", None),
):
    if _k not in st.session_state:
        st.session_state[_k] = _default

tabs = st.tabs([
    "Ask ICE",
    "Case Overview",
    "Goals & Progress",
    "Hypothesis Board",
    "Evidence & Research",
    "Cross-Domain Lab",
    "Investigation Trace",
    "Decision Debugger",
])

with tabs[0]:
    render_ask()
with tabs[1]:
    render_overview()
with tabs[2]:
    render_goals()
with tabs[3]:
    render_hypotheses()
with tabs[4]:
    render_evidence()
with tabs[5]:
    render_discovery()
with tabs[6]:
    render_trace()
with tabs[7]:
    render_debugger()