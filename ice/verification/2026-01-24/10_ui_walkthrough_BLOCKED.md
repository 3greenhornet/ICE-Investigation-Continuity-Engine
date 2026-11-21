# Stage 10 — UI walkthrough: BLOCKED in this sandbox

**Status: blocked-user-side** (requires the Streamlit UI container + a browser;
this sandbox has neither — streamlit is not installed and there is no
interactive browser/display).

## Why blocked

The UI walkthrough (doc Part 4 §11) is manual: open `http://localhost:8501`,
walk all five screens, check each box. No Docker → no `ice-ui` container; no
browser in this sandbox.

What WAS verified here (static — the panels exist and are wired):

- Every panel the doc's §11.2 checklist names is present in the screen
  sources (fix-2-b work; asserted by the fix2b harness (f) greps, 86/86):
  - `ui/screens/case_overview.py`: case-type badge, **Gap Priority
    Distribution** (renamed from "Investigation Debt", §8.3), **Thread Debt**
    panel, **The system noticed** panel (§5.3), Next Recommended Action,
    Active Contradictions, Leading Hypothesis.
  - `ui/screens/hypothesis_board.py`: origin badges (§11.3), **Proposed
    Claim-Hypothesis Edges** review panel with Accept/Reject (§10.5), Version
    History, Supporting/Contradicting Claims.
  - `ui/screens/evidence_graph.py`: `[🤖 {source_llm}]` badge on
    MODEL_INFERENCE rows (§2.6/§11.4), quality badges, provenance chain.
  - `ui/api_client.py`: all 6 new methods with correct URLs (suggestions,
    threads/debt, proposed-edges list/accept/reject, from-llm).
- The UI modules compile and import under the streamlit stub
  (`01_imports.txt` — ui + ui.screens are in the sweep's 148).

## What the user must run (doc Part 4 §11, verbatim steps)

```bash
# §11.1 the UI must answer 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:8501     # [200]
# If not 200: docker compose logs ui --tail 50 ; docker compose restart ui ; retry
```

Then open `http://localhost:8501` in a browser and walk this checklist
(doc §11.2, adapted verbatim — record in `verification/<date>/10_ui_walkthrough.md`):

- **Case Overview** — Case Selector dropdown populated; selected case renders
  title/objective/status/deadline; Leading Hypothesis (or "No hypotheses yet");
  Gap Priority Distribution buckets; Thread Debt section; Active
  Contradictions; Next Recommended Action; "The system noticed" suggestions.
- **Hypothesis Board** — list sorted by confidence desc; expanders show ID,
  status, origin badge; Version History / Supporting Claims / Contradicting
  Claims / Proposed Claim-Hypothesis Edges sub-sections render (proposed edges
  empty if NLI extra not installed).
- **Evidence Graph** — evidence list with quality badges and `[🤖 source_llm]`
  badges on MODEL_INFERENCE rows; provenance chain on click; nodes/relations
  visible.
- **Investigation Trace** — event-type filter works; events show timestamp,
  event_type, seq, payload preview; "All" shows everything.
- **Decision Debugger** — decision dropdown populated; selected decision
  renders type/confidence/rationale/recommended action; Top Factors;
  Alternatives Considered.

If a screen fails: `docker compose logs ui --tail 100` (common causes per doc
§11.3: API unreachable → check `/api/v1/health/ready`; pydantic shape mismatch;
missing api_client function). Gate (§11.4): 200 + all 5 screens render + every
checkbox `[x]` or explicitly waived.