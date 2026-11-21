"""app/routes/ — FastAPI route modules for the ICE API (spec Part 4 §13.6).

One module per domain: cases, evidence, claims, hypotheses, contradictions,
gaps, investigation, actions, decisions, replay, health, plus the
Hari ICE improvements pass reasoning module (induction, predictions,
timeline, ACH/causal, workspace, search, plan, diagnostics). All handlers are
async, at most 20 lines of body, and delegate to case_engine/ via app/deps.
"""