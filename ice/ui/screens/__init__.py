"""ui/screens/ — the five Streamlit screens (spec §15.3–§15.7).

case_overview, hypothesis_board, evidence_graph, investigation_trace,
decision_debugger. Every screen renders via a module-level ``render()``
function and talks to the API through ui.api_client only.
"""