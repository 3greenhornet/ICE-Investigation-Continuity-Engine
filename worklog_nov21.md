
## 2026-11-17 — goal system + research mode (closing the two deferred streams)

User called out that pass 1 deferred two of the document's central
streams despite the preamble's "implement even if wrong" instruction.
Both now shipped as real code:

- case_engine/goals.py + migration 021 (project_goals, goal_edges,
  goal_progress): the Evolving Project Model — 13 intent kinds, clarity
  levels 1-4 (Level-1 vague = valid state), origin as epistemic category
  with user-only promotion, supersede chains (never delete), 9 typed
  edge relations + dependency breadth, evidence-gated maturity ladder,
  intent profile (8 slots, open slots surfaced), GORE-lite decomposition
  (proposals only), honest reconcile (intentional_evolution requires a
  user-recorded chain — keyword evidence can never produce it).
- case_engine/research.py: STORM-lite perspective discovery (claim/evidence-
  mix-derived + always-on defaults, unexplored flagged), perspective-guided
  questions the evidence does NOT answer become real information gaps
  (dedup'd, capped, never auto-executed), tiered synthesis outline
  (established/contested/interpreted, citations kept, marginal-value
  signal reported not decided), one-call research_brief.
- 6 event types, 17 API routes, 2 CLI groups (ice goals / ice research);
  mechanism lab GOAP entry defer -> adapt; api_smoke extended over the
  goal + research lifecycles; fresh-DB verify chain fixed (apply_ice_
  migrations to the shared database; make_dsn now prefers a live
  DATABASE_URL over spinning a second pgserver).
- Environment was recycled (venv lost deps, /tmp wiped): reinstalled,
  re-initialized pgdata, re-ran everything from scratch.
- State: 288 passed + 5 skipped (was 274+5); verify_part2/3 PASS;
  benchmark honest+legacy reproduce the recorded numbers exactly; API
  smoke ALL PASS; download/GAP-ACCOUNTING.md written — the stream-by-
  stream honest status of the whole document and the ranked main
  concerns.

## 2026-11-16 (later) — post-delivery re-verification pass

The previous session was interrupted mid-work. Re-running the entire
verification chain from scratch (fresh venv; embedded Postgres + pgvector
rebuilt; suite, verify scripts, benchmark, API smoke all re-executed)
found and fixed what the interruption left behind:

- Benchmark fault-class metric was never wired into report(); every
  offline run crashed with KeyError after the expensive part finished.
  Added the aggregate + canonical REPORT_KEY_ORDER + crash-proof printer
  + drift-guard unit tests (tests/unit/test_benchmark_metrics.py).
- Fault-class accuracy was recorded as 0.6 in the self-eval/README — a
  number no run ever produced (it was decision correctness). Corrected to
  the reproducible 0.2 everywhere, with the correction documented.
- test_execute_action_transitions_to_completed was not hermetic: it
  passed only on a virgin DB and only the first time it ran (the RETRIEVE
  executor's cross-case fallback found the test's OWN leftovers from
  previous runs). Fixed with per-run unique tokens; the cross-case
  fallback is now pinned by its own test.
- The suite had no per-session DB isolation: sessions shared one database
  and never cleaned up (2000+ leftover cases); global-state tests flipped
  as pollution grew. conftest now creates a fresh database per session
  (dropped at exit). Pitfall found on the way: the fixture resolves
  inside db_pool's event loop, where asyncio.run() is illegal — admin
  statements run on a worker thread instead.
- The explore-ladder test's green was an accident (half-closed pool made
  the recency query throw -> "unknown" -> Rule 4 by chance). Rewritten
  with an explicit Rule 4 setup; Rule 3 (stale -> ABSTAIN) pinned by a
  new companion test.
- verify_all.sh added: one-shot chain (suite + verify scripts +
  benchmark + API smoke), restarts the embedded Postgres if the platform
  reaped it.

State after this pass: 274 tests green twice in a row on a warm server
still carrying 2066 legacy cases, verify_part2/3 rc=0, benchmark
honest+legacy rerun (numbers reproduce exactly), API smoke ALL PASS, zip
rebuilt, all four download/ artifacts refreshed.

## 2026-11-16 — Hari ICE "addition and improvements" pass

Implemented the improvements document end-to-end plus independently found
gaps; see CHANGES.md for the complete register. Highlights:

- Phase 0 harness fixes (conftest vector bootstrap; action.py get_case
  NameError — every debug-mode action generation was broken and invisible
  while integration tests were skipped).
- P0 benchmark rebuilt: no answer leakage, pair-matched contradictions,
  real decision correctness, calibration, first-ever real run
  (benchmark/results/).
- P1: all 10 action executors real; outcome semantics (no
  completed-on-empty); idempotent execution.
- P2: induction engine (QD archive, tournament, deterministic offline
  path, grounding links) + prediction records with Brier scoring.
- P3: true action-conditioned EIG + discriminating-test planner;
  compute_eig renamed uncertainty_priority_heuristic.
- P4: timeline reconstruction + causal layer (reviewed edges, ACH matrix,
  intervention queries).
- P5: 5 new endogenous sources + global candidates; workspace with
  portfolio allocation, refractory decay, observe-only.
- Audit corrections: counterfactual replay rebuilds inference; replay
  covers more event types; transactional event emission; 5-axis evidence
  credibility; source-aware quality caps; independence hardening; real
  explore ladder; prompt registry + LLM role routing + token ledger;
  consolidated-cycle bridge (ICE_CONSOLIDATED_CYCLE).
- Diagnostics: introspection (DFG process mining), trajectory
  (first-failure localization), memory diagnostics (encode/retrieve/reason).
- Adaptive plan manager (superseded-not-deleted) + mechanism lab seeded
  with the document's 16 donor mechanisms.
- 30 new integration tests (265 total green); verify_part2/verify_part3
  PASS offline; 85 API routes; CLI reasoning group.

Deviations from prior specs are all recorded in CHANGES.md (two legacy
tests updated to the new outcome/counterfactual contracts — they encoded
exactly the behaviors the improvements document mandated removing).
