# CHANGES.md — Hari ICE "Addition and Improvements" pass

This pass implements the improvements document's roadmap (P0–P7), its
second-pass audit corrections, and the donor-mechanism directions, plus
gaps found independently during implementation. The four-plane
architecture, the event allow-list discipline, the `_emit_event` contract
and the `_row_to_*` anti-schema-drift mapping are all preserved; every
deviation is listed here (the fixes-doc convention).


---

## Recursive loop, abstract-goal checks, web filter, cross-domain discovery & Streamlit UI (2026-11-28)

- **Closed ICE ↔ RepoIntel improvement loop (`LAB_PROPOSE`):**
  - Migration **023_lab_propose_action.sql** adds `'LAB_PROPOSE'` to `actions.action_type` CHECK constraint (registered in `conftest.py` and `scripts/run_benchmark_offline.py`).
  - `case_engine/schemas.py` (`ActionType`) & `case_engine/action.py` (`LabProposeExecutor`, `research_to_lab_proposal`, wired into `default_executor_for` before `RepoIntelExecutor`) enable generating isolated RepoIntel Lab sandbox proposals (`files=[]` triggers Lab auto-discovery). Execution requires `AUTONOMY_LEVEL >= 1`, `approval_class="REVERSIBLE"`, and never calls `/apply`.
  - Route `POST /api/v1/repointel/lab-proposals` exposes `research_to_lab_proposal`.
- **Abstract-goal decomposition & measurable progress deltas (`repointel_client.py` + `goals.py`):**
  - `RepoIntelClient.decompose_goal_checks()` splits abstract goals and `success_criteria` into capability checks combining hybrid semantic search (`_semantic_candidates`), IDF-ranked symbol/docstring search, stub detection (`_is_trivial_stub` -> `"scaffolded"`), callers/tests, and blocking audit findings.
  - `goals.sync_maturity_from_code()` compares current metrics (`checks_passed`, `mean_check_score`, `health_score`, `blocker_critical_findings`, `test_count`) against prior assessed evidence (`_compute_progress_deltas`) and records `progress_status` (`baseline | improved | regressed | unchanged`) + `deltas`.
- **Adjacent-possible ("would-be-nice") follow-on goal proposals (`goals.py` + `app/routes/goals.py`):**
  - `propose_adjacent_goals()` and `POST /cases/{case_id}/goals/adjacent-proposals` synthesize deduplicated `origin='ice_proposal'` follow-on goals from matured goals, RepoIntel audit/defect hotspots, and pending cross-domain mechanisms.
- **Web research noise filtering (`web_research.py`):**
  - Added `score_research_relevance()` and `filter_research_items()` inside `ingest_research(min_relevance=0.18)` to drop blockwalls/403/captcha fluff, duplicate canonical URLs, and low-overlap boilerplate before ingesting URL evidence.
- **Cross-domain discovery & benchmark (`discovery.py` + `benchmark/cross_domain_cases.json`):**
  - Added `semantic_scholar_search()` (with rate-limiting, cache, and optional `SEMANTIC_SCHOLAR_API_KEY`) to `openalex_field_census` and `field_sources`.
  - Added an 8-case cross-domain discovery benchmark (`benchmark/cross_domain_cases.json`) anchored by chatbot repetition -> IR/Recommender Systems (MMR/DPP) / Behavioral Ecology.
- **ICE Streamlit UI (`ui/`):**
  - Upgraded `ui/api_client.py` (`run_async`), `ui/streamlit_app.py`, `case_overview.py`, `evidence_graph.py`, and added `ui/screens/goals_board.py` (goals, RepoIntel maturity sync + deltas, adjacent proposals, draft Lab action) and `ui/screens/discovery_lab.py` (cross-domain discovery + Mechanism Lab reuse/adapt/reject feedback training the Field Bandit).
  - Kept UI strictly HTTP-only (`tests/unit/test_streamlit_ui.py`).
- **Offline integration reliability & induction resilience (`induction.py`, `retrieval.py`, `test_action_lifecycle.py`, `test_reasoning_loop.py`):**
  - Caught LLM exhaustion inside `induce_hypotheses` to fall back to `_deterministic_candidates`; added `MIN_VECTOR_SIMILARITY = 0.58` cutoff on vector search; fixed 6 integration test LLM-mock/env dependencies.

---

## Web research, goal-driven investigation, real RepoIntel evidence (2026-11-28)

Running ICE against a real Postgres database and a real goal for the first time on this machine surfaced these issues. Each was fixed in the experimental lane or the projection layer: no new event types and no change to `case_engine/__init__.py`.

- **Web research backend** (`case_engine/web_research.py`, new):
  - SEARCH could only fetch a URL a human typed in, and only with paid Scrapfly or Firecrawl keys; natural-language searches were UNSUPPORTED.
  - It now uses `ddgs` (DuckDuckGo, no key), `trafilatura` (page text) and GitHub repository search (keyword-compressed, because GitHub ANDs terms).
  - LLM query planning (`plan_queries`, logged in the LLM ledger) turns outcome-style goals into technical queries.
  - `SCRAPER=local` is the new default; `ICE_WEB_RESEARCH=0` turns every lookup off.
  - Entry points: MCP `ice_web_research` and `POST /cases/{id}/evidence/web-research`.
- **Intent-based SEARCH routing** (`search_scope`): external-knowledge cues ("alternatives", "library", "benchmark", "latest"…) go to web research, everything else to RepoIntel code search, and an explicit `scope` parameter wins. Previously every non-URL search went to RepoIntel.
- **RepoIntelExecutor:**
  - It created sources and evidence with the invalid types `"CODE"`. Every ingest failed, and the `except` branch then **fabricated evidence ids** (`ev_sim_…`) and reported success. It now uses `CODE_REPO` / `TOOL_RESULT`, and a failed write is reported as a failed action.
  - Queries are distilled from templated gap text.
  - Code search uses RepoIntel's hybrid retrieval API (`GET /api/v1/search/{repo}`) before SQL `LIKE`.
  - Repo scope is taken from the action, or from case titles "RepoIntel: <repo>".
- **`RepoIntelClient` repo scoping:** symbol ids are not repo-qualified, so counts, hubs, callers, impact and code search mixed every indexed repo. Queries are now scoped by `repo` / `REPOINTEL_REPO` (name or id) through CONTAINS edges.
- **Goal-maturity check:**
  - It is now scoped to the repo and uses semantic candidates.
  - Keywords are specificity-ranked with stop words and light stemming.
  - Finding nodes are excluded.
  - Test-reference detection is added.
- **Goals:**
  - Migration **022_goal_attributes** (idempotent) adds `priority` (1–5), `success_criteria`, `tags`, `target_date` and `metadata`, mapped explicitly in `_row_to_goal`.
  - `revise_goal()` changes attributes by supersession, keeping the history and emitting only `goal_created`.
  - `sync_maturity_from_code()` closes the goal-to-code loop: RepoIntel's assessment is ingested as TOOL evidence and recorded as an evidence-backed maturity step.
  - Entry points: routes `/goals/{id}/revise` and `/goals/{id}/maturity/sync`; MCP tools `ice_revise_goal` and `ice_sync_goal_maturity`.
  - The `ice_create_goal` docstring now matches `CLARITY_LEVELS`.
- **Goal-driven research questions:** STORM-lite questions now come from the case's highest-priority active goals. Before, they came from the case objective ("…about Track and investigate code-intelligence findings…"). Gap priority follows goal priority (P1 = 0.8).
- **Research-mode actions:** `compute_gap_priority` returns a flat 0.3 for gaps that discriminate fewer than two hypotheses, which every research question does, so research mode could never pass its 0.4 bar. For such gaps the stored priority is now used (research branch only; the debug branch is unchanged).
- **Induction grounding:** "case focus" dropped stop words, so it no longer reads "and code find for".
- **`code_understanding.explain_why_this_code_exists`:** read the non-existent `CodeSymbol.line_start`; the line now comes from the symbol id.
- **`envfiles.py` (new):** the API and the MCP server load `ice/.env` plus `ICE_ENV_FILES` without overriding existing variables. Before, ICE's only working LLM key was a 20-requests-per-day Gemini free tier.
- **Migration lists:** `scripts/api_smoke.py` and `scripts/run_verify.py` were missing 021; 021 and 022 are now listed there, in `conftest.py` and in `run_benchmark_offline.py`.
- **Tests:**
  - New: `tests/unit/test_web_research_routing.py` (16 tests) and `tests/integration/test_goal_attributes.py` (3 tests).
  - `test_action_lifecycle.py` updated for the keyless default scraper and the no-backend case.
  - Pre-existing integration failures, which reproduce on the untouched HEAD: four reasoning-loop tests and `test_execute_action_transitions_to_completed` need a live LLM or a fresh database. `test_execute_compare_produces_derived_evidence` calls a real LLM and fails when the Gemini quota is exhausted.

---

## Serendipity engine: DPP made real (2026-11-28)

The README, the MCP tool `ice_suggest_next` and `CLAUDE.md` described a "DPP diversity" engine, but `serendipity/diversity.py` only had greedy MMR. `ice_suggest_next` also never ran the engine: it returned the first six goals.

- **`diversity.dpp_select`**: greedy MAP inference for a Determinantal Point Process with the incremental Cholesky update of Chen, Zhang & Zhou (NeurIPS 2018). It uses kernel `L = diag(q)·S·diag(q)` with quality `q = exp(theta·relevance)`, and theta comes from the existing `mmr_lambda` dial. It conditions on items already picked for the batch, so it penalises redundancy against the whole set, where MMR only looks at the nearest item. It is pure Python, bounded by `dpp_max_pool` (default 64), and deterministic.
- **`SerendipityConfig.diversity_method`** is `"mmr"` (default, unchanged behaviour) or `"dpp"`.
- **`ice_suggest_next(case_id, n, context)`** now runs the engine over active goals plus open information gaps, using DPP and Thompson sampling by default (`ICE_SERENDIPITY_DIVERSITY`, `ICE_SERENDIPITY_POLICY`). The bandit state persists in `ICE_SERENDIPITY_STORE` (default `data/serendipity_store.sqlite`).
- **New MCP tool `ice_suggest_feedback(token, signal)`** closes the bandit loop.
- **Tests**: `serendipity-engine/tests/test_dpp.py` (13 tests, including the set-redundancy case where MMR fails), and a tools test in `tests/unit/test_integration_hardening.py`.
- No migrations and no new event types. The engine's own SQLite store is not ICE state.

---

## Cross-project integration hardening (2026-11-28)

This pass changes the projection layer only. There are no migrations, no new event types and no change to `case_engine/__init__.py`. Every state write still goes through `case_engine` ingestion.

- **`app/routes/guide.py` (new)**, mounted at `/api/v1/guide`. It serves the `CodeUnderstandingEngine` over HTTP: `walkthrough`, `import-learnharness`, `plan`, `step`, `probe`, `evaluate`, `diagram` and `explain`. Before this, the engine was reachable only through the stdio MCP tools, and RepoIntel's HTTP calls always fell back. Calls are serialised with a process lock, and the routes are `def`, so they run in the threadpool. `CodeUnderstandingEngine._save_to_disk` now writes atomically (temp file + `os.replace`), so concurrent saves cannot leave a half-written store.
- **`POST /api/v1/repointel/event`: deliberate contract change.** It returned `investigation_opened: True` for HIGH/CRITICAL findings without persisting anything. Now:
  - `flagged_for_investigation` reports the flag.
  - `investigation_opened` is True only when the event was written as `TOOL_RESULT` evidence. That happens when `case_id` is given, via `_create_source(..., "TOOL")` and `ingest_evidence`.
  - `persisted_evidence_id` is returned.
  - `TestFailed` events are also persisted.
  - An unknown `case_id` is reported in `error` (HTTP 200, not retryable). `DatabaseUnavailableError` still yields a 503, which tells RepoIntel to retry.
  - Severity matching is case-insensitive.
  - `tests/unit/test_repointel_client.py::test_repointel_event_sync` and `scripts/e2e_cognitive_smoke.py` were updated to the honest semantics.
- **`EvidenceCreate`** gains optional `source_type` / `source_uri`, used only when `source_id` is absent. The default is still `HUMAN`, so existing callers see no change. RepoIntel sends `TOOL`.
- **`case_engine/repointel_client.py`**:
  - DB discovery order is `REPOINTEL_DB`, then `REPOINTEL_DIR`, then the workspace sibling `../../repointel-ai-2`, then the legacy developer path (last).
  - `get_file_snippet` now rejects drive-letter and UNC paths, which `os.path.join` silently honoured, so `C:/Windows/win.ini` was readable through `/api/v1/repointel/file`. It also performs a realpath containment check, refuses secret-bearing files (`.env*` except `.env.example`, keys, credentials, `*.db`), and caps ranges at 2000 lines.
  - The SQLite read-only connection now has a busy timeout (`REPOINTEL_DB_TIMEOUT`).
- **`app/routes/repointel.py`**: the 18 handlers that never `await` are now `def`, so their blocking SQLite reads no longer stall the event loop under concurrent load.
- **`db/connection.py`**: emoji `print()` calls are replaced with logging. On Windows, `print()` of non-cp1252 characters to a pipe or file raises `UnicodeEncodeError`, which aborted the FastAPI lifespan. Pool bounds are configurable (`DB_POOL_MIN` / `DB_POOL_MAX`, default max raised from 5 to 10).
- **New env vars** in `.env.example`: `REPOINTEL_URL`, `REPOINTEL_DB`, `REPOINTEL_DIR`, `REPOINTEL_DB_TIMEOUT`, `AUTONOMY_LEVEL`, `DB_POOL_MIN`, `DB_POOL_MAX`.
- **Tests**: `tests/unit/test_integration_hardening.py` (28 tests, marker `unit`).

---

## Goal-system + research-mode phase (2026-11-17) — closing the two
## biggest deferred streams from the document

The first pass implemented P0–P5 and the audit register but DEFERRED two
of the document's central late-phase streams to the mechanism lab. The
document's own preamble makes that a deviation from the user's explicit
instruction ("do not defer anything that has potential — implement even
if wrong so the idea survives in code form"). This phase implements both
streams as real code.

### A. The goal system — the Evolving Project Model

New module `case_engine/goals.py` + migration `021_project_goals.sql`
(3 tables: `project_goals`, `goal_edges`, `goal_progress`):

- **Typed intent nodes** with the document's full kind vocabulary:
  goal / need / actor / scenario / requirement / quality_objective /
  constraint / assumption / decision / alternative / risk / open_question
  / milestone (the object table from the Evolving Project Model section).
- **Clarity levels 1–4** (exploratory/vague → emerging → partially
  specified → defined objective). A Level-1 goal is valid state
  (`status=exploring`), not an error — ICE must work "before a
  repository exists".
- **Origin = epistemic category** (developer_statement /
  developer_confirmed / ice_inference / ice_proposal / repo_observation),
  enforced by CHECK constraints. `set_goal_status(decided_by='user')`
  PROMOTES an ice_inference/ice_proposal to developer_confirmed; the
  reverse never happens — the document's "never silently convert an
  inference into an accepted developer decision".
- **Supersede chains, never deletions**: `create_goal(supersedes=X)`
  marks X superseded with a forward pointer and bumps the version —
  same contract as the plan manager.
- **Typed edges** (`goal_edges`, 9 relations: refines, depends_on,
  constrains, motivated_by, addresses, conflicts_with, supersedes,
  derived_from, challenges) so "which goals depend on this / what does it
  depend on" is answerable (`goal_dependency_breadth`).
- **Evidence-backed maturity ladder** (`goal_progress`): no_candidate /
  scaffolded / candidate / connected / tested / verified /
  runtime_observed, where anything above `scaffolded` REQUIRES non-empty
  `evidence_ids` (ValueError otherwise) — "a class existing does not
  prove a feature works; a test file existing does not prove a test has
  passed".
- **`intent_profile()`** — the document's initial intent profile: 8
  slots (problem, users, outcomes, scenarios, quality objectives,
  constraints, assumptions, open decisions), each marked filled/open with
  the matching goal rows; the OPEN slots are the next questions to ask.
- **`decompose_goal()`** — GORE-lite decomposition into 5 candidate
  open-questions per goal, all `origin=ice_proposal` + `refines` edges:
  proposals, never decisions.
- **`reconcile_goals()`** — intent-vs-implementation report classifying
  each active goal/need/requirement/milestone/decision as aligned /
  accidental_divergence / intentional_evolution / temporary_deviation /
  unresolved / not_evidenced. Anti-self-deception rule (same family as
  plan.reconcile_plan): **intentional_evolution requires a user-recorded
  supersede chain or explicit confirmation — keyword evidence alone can
  never produce it.** Honest default: unresolved/not_evidenced.

### B. Research mode — the start of the research PROCESS

New module `case_engine/research.py` (no new tables — it wires into
existing gaps/claims/contradictions):

- **`discover_perspectives()`** (STORM-lite): perspectives derived from
  the actual claim-type mix and evidence-type mix PLUS six default lenses
  (practitioner, empirical, skeptical, historical, stakeholder,
  scalability) "nobody had to think of" — with per-lens grounded-claim
  counting and unexplored/partially_explored status. The STORM insight:
  follow-up questions should come from angles the current evidence does
  not speak to at all.
- **`generate_research_questions()`**: per perspective, questions the
  evidence does NOT answer (token-overlap answered-check, HONEST NOTE:
  heuristic) become REAL information gaps via the existing
  `information_gap.create_gap` — deduplicated across runs, capped,
  never auto-executed. ICE proposes; the existing gap/action machinery
  takes over.
- **`research_synthesis_outline()`**: separates epistemic tiers —
  established (observation/measurement claims outside active
  contradictions) / contested (inside contradictions, with contradiction
  ids) / interpreted (interpretation/assumption/prediction claims) —
  every entry keeps its citations, plus an open-gaps section and a
  marginal-value signal (open-to-established ratio) that is REPORTED,
  not decided.
- **`research_brief()`**: the whole pass in one call (perspectives →
  questions/gaps → outline) — the Open-Deep-Research-style supervisor
  slice that runs inside one case.

### C. Wiring

- 6 new event types (`goal_created`, `goal_status_changed`,
  `goal_edge_added`, `goal_maturity_assessed`,
  `goal_decomposition_proposed`, `research_questions_generated`), all in
  the EVENT_TYPES allow-list (spec §7).
- 17 new API routes (`app/routes/goals.py`): goals CRUD + supersede +
  edges + dependencies + maturity + decompose + intent-profile +
  goal-reconciliation; perspectives, research-questions, research-outline,
  research-brief.
- 2 new CLI groups: `ice goals <add|list|show|status|supersede|decompose|
  maturity|intent-profile|reconcile>` and `ice research
  <perspectives|questions|outline|brief>`.
- conftest + `scripts/run_benchmark_offline.py` apply migration 021
  (idempotent); `scripts/apply_ice_migrations.py` picks it up
  automatically (globs `db/migrations/*.sql` >= 012).
- mechanism_lab GOAP entry updated: decision defer → adapt (goal-state
  half shipped in goals.py; precondition/effect chains still deferred).
- API smoke test extended: goal lifecycle (create → decompose → promote →
  intent-profile → reconcile) and research lifecycle (perspectives →
  questions → outline) exercised end-to-end.

### D. Tests + honest notes

- 14 new integration tests (`tests/integration/test_goals.py`,
  `test_research.py`): clarity levels, Level-1 validity, supersede
  chains, origin promotion (user promotes / ice cannot self-promote),
  maturity evidence requirement, typed edges + dependency breadth,
  intent-profile slot accounting, decomposition-as-proposals,
  reconcile honesty (aligned / not_evidenced / intentional-evolution-
  only-via-user-chain), perspective grounding, question→gap dedupe,
  tier separation, brief end-to-end.
- HONEST NOTES: reconcile/goal evidence matching is keyword-coverage
  (same as plan reconciliation) — a heuristic the LLM critique path can
  replace without changing the contract; the research question templates
  are deterministic phrasing, not LLM-generated; the LLM paths for
  decomposition phrasing and perspective naming are natural upgrades,
  not yet wired.
- Suite: 288 passed, 5 skipped (LLM-gated eval), against real Postgres.

---

**Verification status of this pass** (the audit's own distinction —
implemented / wired / tested / runtime-verified):
- 274 automated tests pass against a real Postgres 16 + pgvector (162
  unit-marked + 112 integration-marked by pytest marker, + 5 LLM-gated
  eval skips; includes the 30 new reasoning-loop tests and 9 hardening
  tests from the post-delivery re-verification below).
- Every test session now runs against its OWN fresh database (created at
  session start, dropped at session end) — see hardening item 4.
- `scripts/verify_part2.py` and `scripts/verify_part3.py` PASS **offline**
  (previously BLOCKED — they required embedding/LLM credentials).
- The benchmark **ran end-to-end offline** with real numbers for the first
  time (`benchmark/results/offline_*.json`), in both honest (no-leakage)
  and legacy (seeded) modes; the report now includes the fault-class
  accuracy aggregate as a first-class metric.
- One-shot full chain: `bash /home/z/my-project/scripts/verify_all.sh`
  (suite + verify scripts + benchmark + API smoke).

---

## Post-delivery hardening (2026-11-16, found by re-verifying in a fresh
## environment — the previous session was interrupted mid-work)

Re-running the whole verification chain from scratch (fresh venv, rebuilt
embedded Postgres + pgvector) exposed three gaps the interrupted session
never got to fix or record:

1. **Benchmark fault-class metric was never wired** —
   `benchmark/runner.py` recorded per-case `leading_matches_fault_class`
   flags, `scripts/run_benchmark_offline.py` printed a
   `hypothesis_fault_class_accuracy` key, but `benchmark/metrics.report()`
   never produced it: every offline benchmark run crashed with KeyError
   AFTER the expensive evaluation had already finished. Fixed by adding
   the aggregate, exporting a canonical `REPORT_KEY_ORDER` (single source
   of truth so printer and report can never drift apart again), making
   the printer degrade to `n/a` instead of raising, and adding a
   drift-guard unit test (`tests/unit/test_benchmark_metrics.py`) plus
   printer-tolerance tests.
2. **Fault-class accuracy was misreported as 0.6** — no recorded run ever
   produced that number (it matched decision correctness, a different
   metric); the reproducible value across all runs is 0.2 (1/5). README
   and the self-evaluation report now carry the corrected figure with the
   correction documented in place.
3. **`test_execute_action_transitions_to_completed` was not hermetic** —
   it passed only the first time it ever ran: on any populated database
   the RETRIEVE executor's legitimate cross-case fallback found other
   cases' evidence; worse, a fixed sentinel token made each rerun find
   the test's OWN leg-2 evidence from previous runs (tests do not clean
   up). Fixed with per-run unique tokens; the cross-case fallback path is
   now pinned explicitly by a new test
   (`test_execute_retrieve_cross_case_fallback`) instead of being an
   invisible dependency.
4. **The suite had no per-session isolation** — conftest shared one
   database across sessions and never cleaned up: 2000+ leftover cases
   accumulated, and tests that legitimately read global state
   (`test_cross_case_connections`, the explore ladder) flipped outcomes
   as pollution grew. The suite was green only on a virgin database.
   conftest.py now gives every session its OWN database (CREATE at
   session start, DROP WITH (FORCE) at exit; falls back to the shared DB
   only if CREATE is not permitted). First attempt silently degraded:
   the fixture is resolved lazily from inside db_pool's event loop, so
   `asyncio.run()` raised "cannot be called from a running event loop" —
   the admin statements now run on a dedicated worker thread (the
   no-running-loop check is per-thread), which works from any resolution
   context. Verified by running the full suite green twice on a server
   still carrying 2066 legacy cases.
5. **The explore-ladder test's green was an accident** —
   `test_decide_explore_always_investigate_with_hypotheses` expected
   Rule 4 (INVESTIGATE) but its setup actually satisfied Rule 3's ABSTAIN
   precondition (hypotheses, no recent events, no workspace candidates).
   It passed only because a half-closed pool made the recency query
   THROW, which the engine treats as "unknown, do not claim staleness"
   (recent_events=1) — selecting Rule 4 by chance. Rewritten to set up
   Rule 4's precondition explicitly (hypotheses + a recent
   evidence_ingested event), and Rule 3 is now pinned by a companion test
   (`test_decide_explore_rule3_abstain_when_stale`) so the whole ladder
   contract is covered instead of one lucky path.
6. **The 7 new metric tests initially shipped unmarked** — CI selects jobs
   by marker (`-m unit` / `-m integration`), so unmarked tests would have
   run in NO job. Now `pytestmark = pytest.mark.unit`.

Re-verification after the fixes: 274 tests green twice in a row on a
warm server still carrying 2066 legacy cases, verify_part2/3 rc=0,
benchmark honest+legacy rerun end-to-end (numbers reproduce the prior
runs exactly — deterministic), API smoke ALL PASS.

---

## Phase 0 — correctness first (audit: "Make ICE's current results trustworthy")

### Harness bugs fixed (found by actually running the integration suite)
1. **conftest.py**: `register_vector` (the pool init callback) introspects
   `public.vector` at connection time, but the pool was created BEFORE
   `CREATE EXTENSION vector` ran — every integration fixture errored with
   `ValueError: unknown type: public.vector` on any database without the
   extension pre-created. The extension now bootstraps on a plain
   connection first (mirrors `db/connection.init_db`).
2. **case_engine/action.py**: `get_case` was imported function-locally in
   `generate_actions` but referenced by `_generate_actions_debug` — EVERY
   debug-mode action generation raised `NameError` (invisible while the
   integration suite was skipped; caught the moment it ran).
3. **conftest.py**: migrations 018–020 are re-applied on warm schemas
   (they are fully idempotent); 012–017 remain fresh-database-only.

### Benchmark rebuilt (P0: "Fix evaluation before expanding capabilities")
- **No answer leakage** (`benchmark/runner.py`): the engine receives ONLY
  case.json + evidence. Hypotheses are GENERATED by the induction engine;
  `hidden_resolution.json` is opened only by the evaluator AFTER the run.
  `--seed-from-resolution` (CLI) preserves the old leaking behavior for
  comparison runs only.
- **Pair-matched contradiction metrics**: detected PAIRS (claim ids +
  statements) are recorded; precision/recall match expected pairs by
  bidirectional statement overlap (was count comparison).
- **Decision correctness with real semantics**: ACT is correct only when
  the leading hypothesis actually matches the root cause (was "ACT or
  INVESTIGATE == correct").
- **New metrics** (`benchmark/metrics.py`): hypothesis top-1 / top-3 /
  fault-class accuracy, hypothesis-set diversity, calibration error
  (ECE), abstention quality (incl. wrong-leading-ACTed — the most
  expensive failure, previously invisible), action evidence yield,
  investigation efficiency (confidence per executed action), LLM token
  usage per case. Legacy proxies kept for comparison.
- **Offline determinism**: claim extraction and contradiction detection
  fall back to deterministic extractors when no LLM provider is
  configured (see below), so the benchmark — and the engine — run without
  credentials. Contradiction pair scanning widened offline (first-6-claims
  LLM cost cap → first-30-claims with a shared-subject prefilter; the old
  cap meant the benchmark's own contradictory claims were never compared).
- **First real run** (`scripts/run_benchmark_offline.py`, results in
  `benchmark/results/`): honest-mode baselines recorded, including
  newly-visible failure modes (see README "Honest baseline findings").

## P1 — action execution completed ("every proposed action has a real
executor or is explicitly unsupported")
- **Ten real executors** (`case_engine/action.py`): RETRIEVE (hybrid
  search), COMPARE (structured claim comparison + temporal ordering,
  written as lineage-linked MODEL_INFERENCE evidence under the
  self-confirmation guard), FIND_COUNTEREVIDENCE (contrastive search +
  CONTRADICTS edges; "no counterevidence found" recorded as an informative
  survived-falsification), RUN_TEST (safe deterministic vocabulary only:
  temporal_consistency / state_change / ach_diagnostics — anything else is
  UNSUPPORTED, never a silent no-op), WAIT/ABSTAIN (record wake conditions
  and rationales as information gaps), REVISIT_HYPOTHESIS (recompute +
  induction when explanation-starved), plus upgraded SEARCH/INSPECT/ASK_HUMAN.
- **Outcome semantics** (migration 018): `actions.outcome` ∈
  {EVIDENCE_PRODUCED, INFORMATIVE_NO_EVIDENCE, UNINFORMATIVE, UNSUPPORTED}
  + `result_payload` JSONB diagnostics. Evidence-expecting action types
  can no longer be "completed" on empty results — they land in `failed`
  with a remediation note. Two legacy tests updated to the new contract
  (they encoded exactly the behavior the audit mandated removing).
- **Idempotent execution**: re-executing a completed action returns the
  recorded result (no duplicate effects).

## P2 — hypothesis induction engine ("the biggest gap")
- **`case_engine/induction.py`**: generate → diversify → critique →
  tournament → persist, as a DETERMINISTIC pipeline of typed steps (the
  Robin/Co-Scientist loop without autonomous agents).
  - Every candidate carries the full audit field table: mechanism,
    supporting/contradicting observations, predictions, discriminating
    test, falsification condition, assumptions, provenance, descriptors.
  - **Quality-diversity archive** (MAP-Elites lite): best candidate per
    (causal_level × mechanism_class) cell — a conventional leader cannot
    flood out novel directions.
  - **Deterministic offline path**: 8 fault-class templates (deployment
    regression, resource exhaustion, dependency degradation,
    misconfiguration, capacity load, credential expiry, data
    inconsistency, measurement artifact — the contrarian) grounded in the
    case's observed signals and objective terms.
  - **Grounding links**: each created hypothesis gets claim→hypothesis
    SUPPORTS edges (provenance `origin=ice_induction`, weight = overlap)
    so `recompute_hypothesis_confidence` can rank candidates — without
    them every induced explanation sat at the same ~0.5 confidence.
  - Fires into the cycle via `enable_induction` and the CLI/API.
- **`case_engine/predictions.py`** (migration 018): testable prediction
  records with resolution conditions and probabilities, Brier-scored on
  resolution, calibration report — kept strictly separate from confidence
  ("a belief doesn't become less true because you haven't reviewed it").

## P3 — experiment planning ("tests ranked by expected discriminatory value")
- **True action-conditioned EIG** (`case_engine/next_best_action.py`):
  `expected_information_gain()` computes
  `EIG(a) = H(Θ) − Σ P(o|a) H(Θ|o,a)` from outcome models (attached by
  the planner > LLM > deterministic default). The audit's Test A vs Test B
  property is directly asserted by a new unit test (identical-outcome
  test ≈ 0, discriminating test high).
- `compute_eig` **renamed** to `uncertainty_priority_heuristic` (deprecated
  alias kept); the composite priority uses action-conditioned EIG when
  available and records both in the factor breakdown.
- `plan_discriminating_tests()` attaches predicted outcome models to the
  best discriminating actions (including an ACH-diagnostics RUN_TEST when
  none exists).

## P4 — causal & temporal reasoning
- **`case_engine/timeline.py`**: timeline reconstruction from every
  temporal signal (evidence observed_at/valid windows, claim windows,
  ISO timestamps parsed from content), `explain_state_change` (which
  events fall inside the healthy→failing window — the audit's
  10:00/10:02/10:05 example), `check_temporal_consistency`.
- **`case_engine/causal.py`** (migration 018): causal edges with
  provenance, confidence, assumptions and temporal scope under a
  proposed→accepted/rejected lifecycle; `propose_edges_from_timeline`
  (PRECEDES only — "temporal order alone never justifies CAUSES");
  `predict_intervention` (accepted edges only, honest "unknown" otherwise);
  **ACH matrix** (`build_ach_matrix`) with least-contradicted,
  discriminating-claim and undiscriminating-claim diagnostics.

## P5 — endogenous expansion + investigative workspace
- **`case_engine/endogenous.py`**: five new case-scoped sources
  (unexplained observations, zero-evidence hypotheses, dormant questions,
  contradiction clusters / hidden-common-cause hubs, prediction failures)
  plus `collect_global_endogenous_candidates` (cross-case connections,
  stale investigations) — each individually guarded.
- **`case_engine/workspace.py`** (migration 019): attention competition
  with factor breakdowns (urgency, novelty, refractory), **portfolio
  allocation** (immediate/maintenance/development/exploration/speculation
  quotas — speculation keeps 5% even against urgent work), OBSERVE_ONLY
  disposition (the NO_ACTION_REQUIRED pathway), persisted surfacing
  history powering semantic-refractory decay, human disposition overrides.
- **Real explore decision ladder** (`decision.py`): the documented stub is
  now four rules (no-direction / workspace-candidate / stale-exploration→
  ABSTAIN / default), reason-coded in top_factors.

## Second-pass audit corrections
- **Replay counterfactual fixed** (`replay.py`): `replay_without_evidence`
  now REBUILDS hypothesis confidences from the remaining evidence links
  (same scoring math as recompute, excluded evidence + its supersession
  descendants removed; ALL hypotheses rebuilt including fully-deprived
  ones). One legacy test updated — it asserted the old non-counterfactual
  behavior (influence 0.0 for unevidenced manual bumps; now correctly
  surfaces that the belief had no evidential basis).
- **Replay coverage**: claims, actions (status transitions), decisions,
  threads and induction runs are reconstructed (were ignored).
- **Event consistency**: `_emit_event` accepts a `conn` and the new-plane
  mutators (predictions, causal edges, plan, action execution, decision
  persistence paths) emit INSIDE the caller's transaction — mutation and
  event commit or roll back together.
- **Evidence quality axes** (`evidence.py`):
  `assess_evidence_credibility()` returns the five SEPARATE axes (kind,
  source reliability, observation quality, independence, per-hypothesis
  relevance) with factor breakdowns.
- **Source-aware quality caps**: a type floor applies in full only to
  first-party sources (TOOL/DATABASE/LOG_FILE); DOCUMENT/URL reporting
  "95%" caps at 0.75, HUMAN at 0.90 — content format is no longer
  confused with reliability (the audit's exact example).
- **Independence hardening**: duplicate-cluster members collapse into one
  lineage group even without shared recorded ancestry ("ten articles
  repeating one source are not ten independent confirmations").
- **Offline fallbacks** (deterministic, clearly provenance-marked):
  claim extraction (sentence split + keyword typing, scope
  `{"extraction": "deterministic_fallback"}`), contradiction detection
  (antonym-cue pairs over shared subjects, confidence 0.75), exact-content
  duplicate clustering, lexical thread-reinforcement matching. Every
  fallback is a real engine capability, not a test stub.

## Diagnostics (AgentRx / EvalMem / process-mining inversions)
- **`case_engine/introspection.py`**: directly-follows graph over
  case_events, per-case flow report (execution rates, proposed-never-
  executed by type, loops, ended-with-unresolved-uncertainty), fleet
  comparison by case type.
- **`case_engine/trajectory.py`**: rule-based invariants (execution
  without approval, belief without observation, contradictions without
  follow-up, completed-without-evidence, ACT with open contradictions,
  budget exhaustion) with first-meaningful-failure localization.
- **`case_engine/diagnostics.py`**: encoding/retrieval/reasoning failure
  separation per expected fact, with fix routing.

## Adaptive plan manager (the "crossed out, never deleted" requirement)
- **`case_engine/plan.py`** (migration 019): plan_items with the full
  status vocabulary (incl. superseded/completed_unverified/deferred),
  dependency + evidence links, plan_revisions recording every change with
  reason/evaluation/decided-by. `revise_plan` applies batch changes
  (supersede keeps the original visible with replacement pointers);
  `reconcile_plan` classifies divergences honestly (defaults to
  unresolved; intentional evolution requires a recorded user decision).

## LLM reproducibility & cost
- **`case_engine/prompts.py`**: versioned prompt registry (all existing
  prompt texts preserved verbatim; new induction/critique/tournament/
  outcome-model/causal/contradiction-nature/plan prompts registered).
- **Role-based routing** (`llm.py`): GENERATOR/CRITIC/RANKER/EXTRACTOR
  chains via `LLM_ROLE_<ROLE>` env (cognitive diversity, not just failure
  recovery).
- **LLM ledger** (migration 020): every call records operation, role,
  prompt id/version, model, request/response hashes, token estimates
  (usage metadata when present, else chars/4), latency, success;
  `get_llm_usage()` aggregates per case.

## Mechanism laboratory
- **`case_engine/mechanism_lab.py`** (migration 020): the research ledger,
  seeded with 16 donor mechanisms from the improvements document (ACH,
  FSRS, quality-diversity, hypothesis tournaments, action-conditioned
  EIG, attention portfolios, forecast scoring, process mining, failure
  localization, memory-operation eval, chain of custody, contextual
  bandits, Socratic preference separation, GOAP, ActivityWatch watchers,
  research feeds) — each with original domain/problem, borrowed principle,
  inverted use, adaptations, resource profile and adoption decision. The
  document's research survives in code form.

## Integration surfaces
- **API**: `app/routes/reasoning.py` — 26 new endpoints (induce, cycle,
  predictions, calibration, timeline, state-change, ACH, causal edges,
  workspace, search, contrastive, plan, introspection, trajectory,
  memory-diagnostics, llm-usage, mechanisms, credibility). 85 routes total.
- **CLI**: `ice reasoning <induce|cycle|workspace|ach|timeline|search|
  contrastive|trajectory|introspection|predict|calibrate|plan|mechanisms>`;
  `ice benchmark run` gains `--seed-from-resolution` / `--execute-actions`.
- **Two-pathway consolidation**: `engine/generate.py` case mode can
  delegate its recompute/contradiction/action/decision stages to
  `case_engine.cycle.run_investigation_cycle` via
  `ICE_CONSOLIDATED_CYCLE=true` (with `ICE_CYCLE_INDUCTION` /
  `ICE_CYCLE_WORKSPACE` opt-ins). Default remains the historical inline
  path, bit-for-bit.

## Event allow-list additions (spec §7 table entries)
| event type | payload |
| --- | --- |
| hypothesis_prediction_created | {prediction_id, hypothesis_id, statement, probability} |
| hypothesis_prediction_resolved | {prediction_id, status, score, brier} |
| causal_edge_proposed | {causal_edge_id, relation, cause, effect, confidence, proposed_by} |
| causal_edge_reviewed | {causal_edge_id, decision, note} |
| plan_item_created | {plan_item_id, description, supersedes_item} |
| plan_item_status_changed | {plan_item_id, old_status, new_status, note, decided_by} |
| plan_revised | {revision_id, reason, affected_items, decided_by} |
| workspace_candidate_surfaced | {case_id, selected[{candidate_key, category, score, factors}], observe_only_count} |
| induction_completed | {case_id, candidates_generated, candidates_archived, hypotheses_created, predictions_created, generation_path} |

## Known honest limitations (measured, not hidden)
- The deterministic offline induction identifies fault CLASSES reliably
  (see fault-class accuracy in benchmark results) but phrases candidates
  generically; statement-level top-1 accuracy offline is 0.2. The LLM path
  is where phrasing/ranking quality comes from — the benchmark now
  MEASURES the difference instead of leaking the answers.
- Calibration error offline is high (0.66): token-overlap grounding
  overlinks generic support. This is exactly what the rebuilt benchmark
  was for.
- Contradiction recall offline is 0.2 (antonym-cue detector is
  deliberately conservative); precision 0.5.
- Explore-mode's workspace rule runs one workspace pass inside decide()
  when tables exist; a workspace failure degrades to the old ladder.


---

# PRIOR CHANGE HISTORY (preserved from the Oct 2025-2026 revamp passes)

# CHANGES.md — human-readable change log

Read this first. Full context: `upload/hari ICE revamp implementation.pdf`
(Parts 1–4 + the embedded reviewer audit at the document tail).

---

## ICE Revamp — from hari ICE revamp implementation.pdf (Parts 1-4 + audit) — 2026-10-22

The Hari cognitive-architecture code base was semantically inverted into the
**Investigation Continuity Engine (ICE)**: the same substrate (memory,
attention, curiosity, tick loop), aimed at persistent case state — evidence,
competing hypotheses, contradictions, information gaps, threads, actions,
decisions — instead of simulated inner life. Legacy code is kept in-repo as
historical context; nothing was deleted.

### (a) P0 fixes

| Fix | What | Where |
|---|---|---|
| **A** | Cross-session contamination: promotion paths are session-scoped (and case-scoped in case mode) before thoughts become hypotheses | `engine/promotions.py` |
| **B** | Tick turn corruption: TickRunner no longer collides turn numbers with interactive turns; ticks record `is_tick` bookkeeping (failure mode F4) | `engine/tick.py`, `engine/events.py` |
| **C** | Dead `drive_boost` code path removed from memory retrieval | `engine/memory.py` |

### (b) New packages created

| Package | Contents |
|---|---|
| `case_engine/` | 22 modules: `__init__` (`_emit_event` + event allow-list), `exceptions`, `schemas` (Pydantic v2 contracts), `llm` (fallback chain + circuit breaker), `case`, `evidence`, `claim`, `hypothesis`, `contradiction`, `information_gap`, `investigation_thread`, `evidence_graph`, `provenance`, `action`, `next_best_action`, `decision`, `falsification`, `replay`, `investigation`, `action_value_estimator`, `consistency`, `metrics` |
| `app/` | 15 files — FastAPI service: `main.py`, `schemas.py`, `deps.py`, `errors.py` + 11 route modules (cases, evidence, claims, hypotheses, contradictions, gaps, threads, actions, decisions, investigate, health) |
| `cli/` | `cli.py` — the `ice` CLI (case/evidence/hypothesis/contradiction/gap/thread/action/decision/investigate + `export` and `benchmark` groups) |
| `ui/` | 8 files — Streamlit app: `streamlit_app.py`, `api_client.py`, 5 screens + package init |
| `benchmark/` | Runner, metrics (incl. IE/EU), baselines B1–B6, ablation runner, 5 case fixtures |
| `db/migrations/` | `012_case_model.sql` (11 tables), `013_evidence_graph.sql`, `014_case_events.sql` — 13 ICE tables, all TIMESTAMPTZ |
| `scripts/` | `verify_part2.py`, `verify_part3.py` (live-DB verification), `load_demo_case.py` |
| Ops/docs | `pyproject.toml`, `requirements.txt`, `Dockerfile`, `Dockerfile.ui`, `docker-compose.yml`, `.env.example`, `conftest.py`, `pytest.ini`, `.github/workflows/test.yml`, `tests/README.md` (harness only), `README.md`, `AGENTS.md`, `docs/PROV.md`, `CHANGES.md` (this file) |

### (c) Legacy files modified (surgical, in place)

- `engine/events.py` — added the missing `set_turn()` / `log_hari_expression()`
  methods (fixes an API-drift `AttributeError` crash in `generate.py`), a
  `BACKGROUND_TICK` event type, and an optional `case_events` bridge when
  running in case mode.
- `engine/generate.py` — `TurnPipeline` gained `mode` / `case_id` / `is_tick`
  plumbing, a case-mode hook that drives `case_engine`, and a `case_state`
  return shape aligned with the API's `InvestigationResult` (audit #2).
- `engine/tick.py` — P0-B turn counter.
- `engine/promotions.py` — P0-A session/case scoping.
- `engine/memory.py` — P0-C dead code removal.
- `engine/prediction.py` — added `compute_investigation_surprise` (the
  conversation/investigation surprise split, audit #5).
- `engine/health.py` — added `get_case_health_metrics`.
- `engine/config.py` — `STERILE_MODE` now defaults **true** (ICE product path).
- `models/decision_trace.py` — case fields in (`case_id`, `decision_id`,
  `action_id`), homeostatic fields out (per the spec's inversion table);
  `generate.py` + trace storage adjusted to match.

### (d) Audit gap fixes applied

From the spec tail's 43-gap reviewer audit (12 critical), these were
addressed: **#1** (evidence.metadata column + param), **#2** (pipeline/API
return-shape alignment), **#3** (helper ordering landmine), **#4**
(tool-call budget enforcement in `generate_actions`), **#5**
(investigation-surprise split), **#6** (`background_tick` event), **#8**
(`CaseState.empty()`), **#9** (CaseState recovery fields), **#10**
(`edit_action`), **#11** (CLI `export` group), **#12** (CLI `benchmark`
group), **#13** (Investigation Efficiency metric), **#14** (Evidence
Utilization metric), **#15** (ablation runner), **#16** *partial* — the
interruption/evidence-poisoning archetypes land in benchmark cases 02/04,
stale-evidence remains generic, **#18** (circuit breaker wired into
`_call_llm_json`/`_call_llm_text`), **#21** (`action_value_estimator.py`
shim), **#22** (`run_investigation_cycle` in `case_engine/investigation.py`),
**#23** (quality_score = authority × directness × freshness actually
computed), **#25** (contradiction candidate filtering — kills the O(N²) LLM
call blowup), **#27** (`resolve_gap` idempotency guard), **#31/#32**
(investigation threads wired into the case-mode cycle, registry retained),
**#36** (benchmark runner exercises contradiction detection), **#37**
(wall-clock latency instead of hardcoded 0.0), **#40** (naive datetimes
promoted to UTC), **#43** (public `get_contradiction` + GET-by-ID route).

Improvisations landed: **B** (`case_engine/metrics.py`), **C**
(`case_engine/consistency.py::validate_case`), **D** (`remediation` field in
error responses), **E** (`scripts/load_demo_case.py`), **G**
(`ActionExecutor` protocol + Null/WebSearch/FileInspect/HumanAsk executors),
**I** (LLM seeding helper for reproducible evals), **K** (`AGENTS.md`).

### (e) NOT implemented / deferred (do not look for these)

- **Idempotency keys** on POST endpoints (improvisation A) — not implemented.
- **Schema doc generation** (improvisation F) — not implemented.
- **Prompt centralization** (improvisation H) — prompts remain in their
  modules (`claim.py`, `contradiction.py`, `falsification.py`, …).
- **Cost tracking** (improvisation J) — *partial*: the tool-call budget is
  enforced; token-level cost accounting is not.
- **W3C PROV** — documentation only (`docs/PROV.md`); no code emits PROV.
- **Benchmark archetypes** — ablation runner included, but only two of the
  three PDF archetypes are explicitly covered (see #16 above).
- Phase 2+ integrations (all off / pending): Graphiti, DBOS, MCP, Langfuse,
  omega-audit, DeepEval integration (env placeholders only in `.env.example`).

### (f) Verification status

- The build sandbox had **no Postgres, no pgvector, no LLM keys** — runtime
  verification was impossible there. All Python files, including every
  `case_engine/`, `app/`, `cli/`, `ui/`, and `benchmark/` module, pass
  `py_compile` / import-structure checks; YAML/TOML/INI config files are
  syntax-validated.
- **Runtime verification requires a real deployment:**
  `docker compose up -d --build`, apply migrations 012–014 (see README
  *Migrations*), then run `python scripts/verify_part2.py` and
  `python scripts/verify_part3.py` against the live DB, plus
  `python scripts/load_demo_case.py` for a smoke case.
- Test harness (`conftest.py`, `pytest.ini`, CI workflow) ships **without**
  test files — the tests themselves are the next phase (see `tests/README.md`).
- Known-unknowns, to check on first live run:
  1. Migration DDL 012–014 executes cleanly against an empty pgvector 16 DB
     (never run against a live server in the sandbox).
  2. Docker build details adapted for Debian 12: `libgl1`/`libglx-mesa0`
     replace the spec's `libgl1-mesa-glx` (removed in bookworm); editable
     install uses `--config-settings editable_mode=compat` so packages copied
     after the dependency layer still resolve.
  3. The compose `db/migrations` mount is read-only *reference* — the Postgres
     entrypoint does not execute SQL inside subdirectories; run the
     migrations explicitly on first startup.
  4. Sibling-built interface packages (`app/`, `cli/`, `ui/`, `benchmark/`)
     compile and import in the sandbox but have not been exercised
     end-to-end against live services.
  5. CI's `eval` job needs the `GEMINI_API_KEY` secret configured in the
     repository.

### (g) Final packaging sweep (pre-ZIP)

A tree-wide consistency pass caught and fixed four leftovers before
packaging:

| Fix | What | Where |
|---|---|---|
| `.env.example` restored | The file claimed by the deployment wave was absent from the tree; restored verbatim from spec §19.4 (+ commented Phase 2+ deferred block) | `.env.example` |
| `relevance_base` AttributeError | `AttentionInstrumentation.get_summary()` read a `relevance_base` field that does not exist on `AttentionCalibration`; now reads `intrinsic_relevance_base` (and the summary key is renamed to match) | `engine/attention_instrumentation.py` |
| Missing `ActiveInferencePRM` | `generate.py`'s legacy PRM hook lazily imports `engine.prm_discriminator`, which was never part of the uploaded snapshot; reconstructed as a minimal call-site-contract implementation (expected-free-energy candidate selection, honestly marked as a reconstruction) | `engine/prm_discriminator.py` (new) |
| `calibrate_attention.py` ImportError | It imports `CANONICAL_CONVERSATIONS` from `run_observatory`, which did not define it; added a deterministic 6-conversation suite mirroring the `SimulatedUser` branch categories (greeting, lecture interrupt, why-probe, story hook, assistant test, fact pivot, identity, boredom, disagreement, farewell) | `scripts/run_observatory.py` |

Verification for this pass: `py_compile` clean on all 132 Python files;
AST cross-module import check — 394 intra-repo imports, 0 unresolved;
stubbed import sweep — 122 modules across 15 packages, 0 failures;
runtime smokes for each fix above; all six prior agent harnesses
(ice2a, ice4a-verify, ice4a-live-http, ice2c, ice4c, ice4b) re-run green.

### (h) Second audit pass — remaining spec-audit gaps closed (pre-re-ZIP)

A gap-by-gap re-check of the spec's 43-item reviewer audit against the
shipped tree found four unaddressed items plus a spec-internal
inconsistency; all closed:

| Item | What | Where |
|---|---|---|
| Audit #7 `claim.status` | Claims now carry a lifecycle status (`ACTIVE`/`SUPERSEDED`/`RETRACTED`, CHECK-constrained); new `set_claim_status()` (fetch→no-op guard→UPDATE→zeroes confidence off-ACTIVE→emits a *real* `claim_status_changed` event); surfaced in `Claim` schema, API `ClaimResponse`, and CLI `ice claim status <id> --status ... --reason ...` | `db/migrations/012`, `case_engine/schemas.py`, `case_engine/claim.py`, `app/schemas.py`, `cli/cli.py` |
| Audit #24 dead source-score columns | `_create_source` now accepts optional `authority_score`/`directness_score`/`freshness_score` (validated [0,1], defaults 0.5) and persists them — callers with source knowledge can make `quality_score = authority × directness × freshness` meaningful | `case_engine/evidence.py` |
| Audit #5 wiring | `compute_investigation_surprise` (landed earlier as a function with zero callers) is now wired into the case-mode hook: newest evidence vs leading hypothesis, attached as `investigation_surprise` in the pipeline's `case_state` (additive key; the API `InvestigationResult` shape is untouched) | `engine/generate.py` §7b |
| Dangling ICE flags | The spec declares `ENABLE_CONTRADICTIONS`/`ENABLE_HYPOTHESIS_VERSIONING`/`ENABLE_REPLAY`/`ENABLE_SOURCE_INDEPENDENCE` (§9, §19.4, §21) and its own baselines B4/B5 toggle them — but no engine module ever read them. Now wired, all read **at call time** (so the import-time trap of audit #39 does not apply and B4/B5 are real ablations): disabled contradiction detection returns `None` with zero claim loads/LLM calls; disabled versioning freezes confidences (recompute stub = its existing `None` contract); disabled source-independence counts duplicate evidence at full weight (each item its own lineage group); disabled replay raises `ValueError` → API 400 INVALID_REQUEST | `case_engine/{contradiction,hypothesis,replay}.py` |
| Audit #38 | `benchmark/runner.py::run_all_cases` now runs cases concurrently via `asyncio.gather` + `Semaphore(3)` (order-preserving, per-case fault isolation kept) for Phase-4-scale sweeps | `benchmark/runner.py` |
| §19.6 acceptance 5 | New idempotent `scripts/apply_ice_migrations.py` (applies 012+ SQL files, skipping tables that already exist); the compose `api` service runs it before uvicorn so migrations really do run automatically on first startup (the entrypoint-mount approach cannot work: Postgres ignores SQL in subdirectories) | `scripts/apply_ice_migrations.py`, `docker-compose.yml` |
| Improvisation F | `scripts/export_openapi.py` generates `docs/openapi.json` (44 paths, OpenAPI 3.1.0) from the FastAPI app — shipped in the zip | `scripts/export_openapi.py`, `docs/openapi.json` |
| Audit #39 (documentation) | README now states which flags/env are read at call time vs import time (`ENABLE_FALSIFICATION_LOOP`, `ENABLE_NEXT_BEST_EVIDENCE`, `ACTIVE_FALLBACK_MODELS` + API keys are import-time), and the Migrations section no longer implies `migrate_all.py` applies the 002–011 SQL files (it applies the legacy inline schema) | `README.md` |

Audit items still intentionally NOT implemented (unchanged): #17 PROV
(docs-only, `docs/PROV.md`), #20 legacy-data rename migration (no legacy
deployment exists to migrate), #30 gap-priority auto-refresh (spec says
"no auto-persist"), #33/#34/#35 (spec itself concludes "not a gap"),
#42 temporal `time_a`/`time_b` updates in `resolve_contradiction` (not
PDF-mandated). Improvisation A (idempotency keys), H (prompt
centralization), J (token cost tracking) remain deferred as before.

## Merge from Oct 23 (ICE fixes doc Part 1) — 2026-01-24

Base: this tree (Oct 22). The Oct 23 codebase itself was unavailable in
this environment — the merge specification ("hari ice fixes" document,
Part 1) carries every patch verbatim, so each patch below was applied by
hand onto our files (adapting anchors where our code differs from the
doc's hypothetical anchors; semantics preserved exactly per patch).

### Patches P1–P8 applied

| Patch | What | Where |
|---|---|---|
| P1 | `DEFAULT_QUALITY_SCORES` type-quality floors (MEASUREMENT 0.95 … MODEL_INFERENCE 0.0) + `quality_score: Optional[float]` parameter on `ingest_evidence`; quality is now `max(source_quality, type_default)` (explicit caller override wins, clamped [0,1]) — fixes the 0.5³ = 0.125 default-score depression. `EvidenceCreate` gained `quality_score` AND `metadata` (it had neither); the ingest route threads both through. fin-1's caller-controllable source scores kept intact. | `case_engine/evidence.py`, `app/schemas.py`, `app/routes/evidence.py` |
| P2 | `recompute_hypothesis_confidence` now (a) reads `e.relation` on claim→hypothesis edges — a CONTRADICTS claim inverts polarity instead of silently *raising* confidence (Correction 6); (b) merges direct evidence→hypothesis edges (`support_score += weight × evidence_weight`) via a new `direct_ev_rows` query — unlocks the NLI classifier pattern. Versioning flag gate + sigmoid tail unchanged. | `case_engine/hypothesis.py` |
| P3 | Defensive jsonb parsing: per-module guards in `_row_to_evidence`/`_row_to_source` (metadata, incl. `"metadata" in row` guard), `_row_to_claim` (scope), `_row_to_contradiction` (scope_a/scope_b), `_row_to_decision` (alternatives/top_factors), `compute_decision_attribution` + `_get_events` (payload). | `case_engine/{evidence,claim,contradiction,decision,replay}.py` |
| P4 | `ice investigate run <case_id> --falsify` flag → `enable_falsification=getattr(args, "falsify", False)` in the `_DISPATCHERS` dispatch. | `cli/cli.py` |
| P5 | `EvidenceResponse.metadata: dict` — metadata was write-only (accepted on ingest, dropped from every response). `source_llm` deferred to Part 2 (migration 015). | `app/schemas.py` |
| P6 | `get_case_summary().next_action` now computed by `select_next_best_action` (lazy import inside the function — avoids the case.py ↔ next_best_action.py import cycle) instead of an approximate inline-SQL priority that could disagree with the NBA engine. | `case_engine/case.py` |
| P7 | Decision-attribution 404 — **already landed in fin-0** (`DecisionNotFoundError` when the attribution dict is empty); verified, no change needed. | `app/routes/decisions.py` |
| P8 | New `db/migrations/012a_safety_check.sql` (DO $$ block: RAISE EXCEPTION when legacy `hypotheses`/`contradictions` hold rows) + the three guarded `DROP TABLE IF EXISTS … CASCADE` prepended to `012_case_model.sql`. `scripts/apply_ice_migrations.py` extended with an `ice_migration_history` table (so CREATE-TABLE-less files like 012a — previously skipped forever — apply exactly once) and a `_migration_sort_key` that places `012a` immediately BEFORE `012` (lexicographic order would run the safety check after the drops, defeating it). `conftest.py` applies 012a before 012. | `db/migrations/012a_safety_check.sql`, `db/migrations/012_case_model.sql`, `scripts/apply_ice_migrations.py`, `conftest.py` |

### Shared bugs S1–S2, regression fix R6

- **S1** — `InvestigateRequest.max_actions` removed (the field was never
  honored by `run_investigation_cycle`); no route pass-through existed.
  Zero `max_actions` matches remain in `case_engine/ app/ cli/ ui/
  benchmark/ scripts/ engine/`.
- **S2** — `events_emitted` in the investigation cycle now uses
  `MAX(event_seq)` before + `COUNT(*) WHERE event_seq > before_seq` after
  (spec's Option B): exact under concurrent cycles, where the old
  COUNT(*)-before/after diff double-counted the other cycle's events.
- **R6** — `_is_stagnant` now counts `status IN ('proposed', 'approved',
  'executing', 'completed')`: with only post-approval statuses the
  stagnation→switch-retrieval-strategy path could never fire on a fresh
  case (all actions are still `proposed` inside one cycle).

### `case_engine/investigation.py` → `case_engine/cycle.py`

Renamed to match the Oct 23 codebase (recovery-scan Section 0.6, Option
B — recommended because Part 2 touches this file heavily).
`investigation.py` retained as a re-export shim for backward
compatibility. Content is Oct 22's (bug-free) version; the three known
bugs in Oct 23's `cycle.py` (dead `force_recompute`, ungated
`select_next_best_action`, `COUNT(*)` race) are not ported — the last is
fixed here as S2. Imports updated in `app/`, `cli/`, `scripts/`
(`load_demo_case.py`); the shim keeps any external caller working.

### Disposition decisions

- Nothing was ported wholesale from Oct 23: the Oct 23 directory is
  unavailable in this environment, so every change above was applied from
  the merge specification's verbatim patch code (this is the route the
  document itself prescribes for exactly this situation).
- Files the doc marks "COPY from Oct 23" (`case_engine/evidence.py`,
  `hypothesis.py`, `contradiction.py`, `app/routes/evidence.py`) were
  NOT replaced — our versions carry fin-0/fin-1 audit fixes those
  copies lack (optional source scores, claim status, call-time flag
  gates, metadata threading). The doc's own diff rules (§0.4.1: "Merge:
  Oct 22 base + Oct 23 additions") were followed instead.
- `scripts/seed_demo.py` (Oct 23-only file) is not recoverable without
  the Oct 23 directory; `scripts/load_demo_case.py` remains canonical
  and is rewritten in Part 2 (§6) regardless.
- Migration 012 edit-in-place is the doc's sanctioned exception ("never
  edit shipped migrations" — 012 has not shipped to production).

Verification for this pass: `py_compile` clean on every touched file and
the full tree; the doc's Section 8.1/8.5 grep checks all pass
(3× DEFAULT_QUALITY_SCORES, 2× direct_ev_rows, --falsify present,
before_seq present, 0× max_actions, all five P3 guards, 012a with RAISE
EXCEPTION, 3× DROP TABLE in 012, next_action_dict, DecisionNotFoundError);
stubbed harness `.scratch/fix1_verify.py` green (shim identity, pydantic
fields, DEFAULT_QUALITY_SCORES values, lazy NBA import via AST, migration
sort key); AST import checker and tree-wide stubbed import sweep still
green. Runtime acceptance (docker compose + verify_part2/verify_part3)
remains blocked on a live Postgres/LLM environment as before.

## Part 2 scope-broadening — engine core (2026-01-24)

Implementation of the "hari ice fixes" document Part 2, engine-core scope
(Sections 0–13: Corrections A–D confirmed against our tree — none of
case_type / ingest_external_analysis / origin / source_llm /
case_engine.endogenous existed, so all of it is new work, not a port).

### Migrations

- **015_external_analysis_and_case_type.sql** (doc §1.1, verbatim):
  `evidence.source_llm` (+ partial index `idx_evidence_source_llm`),
  `hypotheses.origin`/`origin_detail` (CHECKed, default 'user'), and
  `cases.case_type` (CHECKed, default 'debug', + `idx_cases_type_status`),
  all `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` — fully
  idempotent.
- **016_contradiction_uniqueness.sql** (doc §9.1, verbatim): generated
  `claim_pair_key` column (`LEAST(claim_a,claim_b) || '/' ||
  GREATEST(...)` STORED), `uq_contradictions_claim_pair` +
  `uq_contradictions_direct` unique constraints, `idx_contradictions_pair`.
  Header documents the dedup precondition: on databases that already hold
  duplicate contradictions, run **scripts/dedupe_contradictions.sql**
  (doc §9.1's DELETE-USING script) BEFORE applying 016.
- **017_proposed_edges.sql** (doc §10.3): proposed_edges table for NLI
  claim→hypothesis edges pending user confirmation, with
  `UNIQUE(case_id, from_node, to_node, relation)` and
  `idx_proposed_edges_case_status`.
- `scripts/apply_ice_migrations.py` needed no logic change: fix-1's
  history table already covers the ALTER-only files 015/016 (no CREATE
  TABLE → apply once + record), and 017's `proposed_edges` table-exists
  check plus the history record both hold. Docstring updated.
  `conftest.py` now applies 012a, 012, 013, 014, 015, 016, 017 (in that
  order) for the test database.

### Feature 2A — external LLM analysis ingestion

- `case_engine/schemas.py`: `CaseType` / `HypothesisOrigin` Literals;
  `Case.case_type="debug"`, `Hypothesis.origin="user"` /
  `origin_detail=None`, `Evidence.source_llm=None` (all defaulted —
  backward compatible).
- `case_engine/evidence.py`: `ingest_external_analysis()` (doc §2.1) with
  structured content, HUMAN source carrying `analyst_llm` metadata,
  MODEL_INFERENCE evidence, prompt/response/context metadata, best-effort
  `UPDATE ... SET source_llm` (migration-015-not-applied tolerance:
  logger.warning, never fatal), and `(LLM, prompt)` SHA-256 dedup
  (`_check_external_analysis_duplicate`, doc §2.7) checked BEFORE the
  source is created. `_row_to_evidence` reads `source_llm` with a
  `row.keys()` guard. **Honest notes in code:** the 0.4 external-LLM
  weight is a placeholder (doc §12 shortcut #1, in
  `hypothesis._evidence_weight`); the HUMAN source type misrepresents the
  LLM origin (shortcut #8); dedup is exact-match, not semantic.
- `case_engine/hypothesis.py::_evidence_weight`: MODEL_INFERENCE /
  MODEL_SUMMARY with `source_llm` set → **0.4**, without → 0.0
  (self-confirmation guard preserved), everything else → quality_score.

### Feature 2B — case_type branching

- `case_engine/case.py`: `create_case(required_confidence: Optional[float]
  = None, case_type="debug")` — 0.85 default for debug, 0.0 for
  research/explore, explicit value wins; `case_type` in the INSERT and the
  `case_created` payload; `_row_to_case` guards a missing column
  (defaults to "debug"); `list_cases(case_type=...)` filter (dynamic
  WHERE, fully parameterized); `get_case_summary` includes `case_type`.
- `case_engine/decision.py`: `decide()` is now a dispatcher —
  `_decide_debug` (the untouched 6-rule ladder; every `_make_decision`
  call now carries `"case_type": "debug"` in top_factors), plus
  `_decide_research` (4 rules: no_hypotheses → active_contradictions →
  high_priority_gaps > 0.5 → ready_to_synthesize ACT) and `_decide_explore`
  (always INVESTIGATE — documented stub, doc §12 shortcut #6).
- `case_engine/action.py`: `generate_actions()` dispatches to
  `_generate_actions_debug` (unchanged), `_generate_actions_research`
  (budget check; gaps[:5] with recomputed priority ≥ 0.4 → SEARCH;
  contradictions[:3] → COMPARE with severity as EIG; endogenous[:3] →
  REVISIT_HYPOTHESIS; no stagnation) and `_generate_actions_explore`
  (debug actions + endogenous[:3] REVISIT_HYPOTHESIS). Honest note: the
  5/3/3 caps are arbitrary (shortcut #4); debug mode deliberately does
  NOT consume endogenous candidates (shortcut #12).

### Feature 2C — executor wiring

- `default_executor_for(action)`: SEARCH→WebSearchExecutor,
  INSPECT→FileInspectExecutor, ASK_HUMAN→HumanAskExecutor, everything
  else→NullExecutor. Honest note: 7 of the 10 action types still execute
  as no-ops (doc §4.6 design note).
- `execute_action`: `executor=None` now dispatches via
  `default_executor_for` instead of the unconditional closed-world
  NullExecutor; an explicit executor argument still overrides.
- `WebSearchExecutor.execute`: missing SCRAPFLY_API_KEY /
  FIRECRAWL_API_KEY now raises an explicit RuntimeError (propagates to
  the action_failed path) instead of silently returning []; non-URL
  targets remain a logged warning + [] (NullExecutor semantics).

### Feature 2D — case-mode endogenous candidates

- New `case_engine/endogenous.py::collect_case_endogenous_candidates`:
  aged investigation threads (>1d, via InvestigationThreadRegistry debt),
  ACTIVE evidence never cited (`extracted_claim_ids = '{}'`, >1h),
  hypotheses stuck at 0.3–0.7 confidence (>2d), active contradictions
  with severity > 0.5 (>2d). Each source is individually try/except-
  guarded and capped at `max_per_source`; thresholds are exported tunable
  constants (`_UNCITED_EVIDENCE_HOURS` etc. — honest note: round numbers,
  shortcut #2).

### Fix 2F — hypothesis lifecycle

- `case_engine/hypothesis.py::update_hypothesis_lifecycle` (doc §7.1):
  CONFIRMED/FALSIFIED terminal → None; falsification via
  `check_hypothesis_falsified(margin=0.5)` → FALSIFIED; active
  contradiction on a supporting claim → CHALLENGED (from
  PROPOSED/SUPPORTED); confidence ≥ 0.7 + PROPOSED → SUPPORTED;
  confidence < 0.3 + SUPPORTED/CHALLENGED → REMAINS_OPEN. Honest note:
  thresholds are uncalibrated heuristics (§7.3).
- `case_engine/cycle.py::run_investigation_cycle` runs the lifecycle for
  every hypothesis right after the recompute step and returns
  `lifecycle_changes` (each transition individually guarded).

### Fix 2G — investigation debt wiring

- `case_engine/next_best_action.py::compute_thread_debt_bonus` (doc
  §8.1): word-overlap thread matching (≥1 shared word — honest note:
  crude proxy, shortcut #3), debt→bonus `min(0.3, max_debt/5.0*0.3)`
  (0.3 cap arbitrary — shortcut #4), gated on
  ENABLE_NEXT_BEST_EVIDENCE, never raises.
- `compute_priority_with_factors`: priority formula gains `+ debt_bonus`
  and the factors dict gains the `debt_bonus` key (rule-11 shape
  otherwise unchanged: eig/impact/falsification/cost/diversity/
  redundancy/risk/numerator/denominator).

### Fix 2H — contradiction idempotency

- `case_engine/contradiction.py::detect_contradiction` (doc §9.2/9.3):
  (1) pre-check on (case_id, claim_a, claim_b) in either order returns
  the existing contradiction before any LLM call; (2) the INSERT uses
  `ON CONFLICT (case_id, claim_pair_key) DO NOTHING RETURNING *` — on a
  race the winner's row is re-fetched and returned WITHOUT a second
  `contradiction_detected` event (exactly one event per actual mutation);
  an INSERT-conflict with no findable row raises RuntimeError.
  ENABLE_CONTRADICTIONS call-time gate and the scope/temporal pre-checks
  are unchanged. Prerequisite noted in the docstring: migration 016 must
  be applied (the ON CONFLICT targets its generated column).

### Fix 2I — NLI classifier (engine parts)

- New `case_engine/nli.py` (doc §10.2, verbatim): lazy RoBERTa-large-MNLI
  load with a `_NLI_LOAD_FAILED` latch, `classify_claim_against_hypothesis`
  returning Optional[SUPPORTS|CONTRADICTS|NEUTRAL], never raises, inference
  in a thread-pool executor. Honest note: surface-text matching
  misclassifies inferential relationships (shortcut #13).
- `case_engine/claim.py::extract_claims` wiring (doc §10.3): after claim
  creation, every new claim × every existing hypothesis is classified;
  SUPPORTS/CONTRADICTS insert into `proposed_edges`
  (`ON CONFLICT ... DO NOTHING`, `model_used='roberta-large-mnli'`,
  flat confidence 0.8 — honest note: the real probability is discarded,
  shortcut #14). Entirely best-effort (logger.debug guards); without the
  `nli` extra installed nothing changes.
- **Event deviation (doc §10.4 option 2):** proposed-edge insert/accept/
  reject emit NO case_events rows — `proposed_edge_accepted/rejected` are
  not in EVENT_TYPES and adding event types is a schema change deferred
  to Part 4; the app-layer accept/reject handlers (fix-2-b) will log at
  INFO level only. Noted in both the migration header and the wiring.
- `pyproject.toml`: new `nli = ["transformers>=4.30", "torch>=2.0"]`
  optional dependency.

### Fix 2J — cleanup (engine part)

- `case_engine/evidence_graph.py::add_edge` docstring now documents the
  `UNIQUE(from_node, to_node, relation)` multi-support LIMITATION and the
  two supported workarounds (metadata reasons list, or separate claims).
  No schema change; `add_edge` remains idempotent on the triple.
- Homeostatic dead-code removal and UI badges are other agents' scope.

Verification for this pass: `py_compile` clean on all 138 .py files; doc
§13 grep checks all green (case_type across schemas/decision/action/case,
ingest_external_analysis, source_llm, origin_detail,
collect_case_endogenous_candidates, classify_claim_against_hypothesis ×3,
update_hypothesis_lifecycle ×4, compute_thread_debt_bonus ×3 +
debt_bonus ×7, claim_pair_key in 016 + contradiction.py, ON CONFLICT,
proposed_edges in 017, default_executor_for, enable_auto_execute +
lifecycle_changes in cycle.py); stubbed harness
`.scratch/fix2a_verify.py` 15/15 (imports, schema defaults, row-mapper
guards, _evidence_weight 0.4/0.0/passthrough, executor dispatch for all
10 action types + WebSearchExecutor failure modes, NLI None-without-
raising + latch, all 4 research rules + explore stub + debug dispatch,
action-generation branches, all lifecycle rules, debt bonus + factors
dict, contradiction pre-check/race/normal/inconsistent paths, migration
ordering + contents, ingest_external_analysis validations + dedup,
create_case defaults + list_cases parameterization, endogenous guards);
`.scratch/final_import_check.py` (416 imports, 0 errors) and
`.scratch/final_import_sweep.py` (127 modules, 0 failures, +endogenous
+nli) still green; `.scratch/fix1_verify.py` still 7/7. Live-Postgres
acceptance (docker compose + verify scripts + the doc §13.1–13.6
behavioral checks) remains blocked on the sandbox environment as before.

## Part 2 surface + operations (fix-2-b/fix-2-c) — 2026-01-24

Completion of the "hari ice fixes" document Part 2 on the projection
planes and operations surfaces. fix-2-b: app/cli/ui (§2.4–2.6, §3.4,
§4.4–4.5, §5.2–5.3, §8.3, §10.4–10.5, §11.3–11.4). fix-2-c: demo script
rewrite (§6), homeostatic dead-code removal (§11.1), documentation
(§13.6), `.env.example` verification. The engine core landed in the
previous section; `docs/openapi.json` regenerated (44 → 50 paths).

### New API routes (fix-2-b)

| Route | What |
|---|---|
| `POST /cases/{case_id}/evidence/from-llm` | external LLM analysis → MODEL_INFERENCE evidence via the `EvidenceService.ingest_external` deps shim; 201; ValueError → 400, missing case → 404; `(llm, prompt)` hash dedup returns the existing evidence |
| `GET /cases/{case_id}/suggestions` | endogenous "the system noticed" candidates (§5.2 shape); pool-down → 503 — an empty list genuinely means "nothing needs attention" |
| `GET /cases/{case_id}/threads/debt` | active investigation threads with computed debt, age, strength, decision impact, uncertainty |
| `GET /cases/{case_id}/proposed-edges` | pending NLI claim→hypothesis proposals, newest first |
| `POST /proposed-edges/{edge_id}/accept` | commits the proposal: `add_edge(..., weight=confidence)` (idempotent on the triple) + status `accepted`/`resolved_at` |
| `POST /proposed-edges/{edge_id}/reject` | status `rejected`/`resolved_at`; the evidence graph is untouched |

Accept/reject log at INFO level only — the documented §10.4 option-2
event deviation (no new `EVENT_TYPES`). Schema additions:
`CaseCreate.case_type` (per-type `required_confidence` defaulting:
Optional, engine-picked 0.85 debug / 0.0 research+explore),
`EvidenceResponse.source_llm`, `HypothesisResponse.origin`/`origin_detail`,
`InvestigateRequest.enable_auto_execute`,
`InvestigationResult.lifecycle_changes` + `auto_executed_action`,
`ExternalAnalysisCreate`, `EndogenousSuggestion`; `GET /cases` gained a
`?case_type=` filter.

### CLI + UI (fix-2-b)

- CLI: `case create --type {debug,research,explore}` (`--required-confidence`
  default 0.85→None so the engine's per-type default applies),
  `evidence add-llm <case_id> --llm/--prompt/--response/--context/--source-uri`,
  `investigate run --auto-execute` (alongside fix-1's `--falsify`;
  auto-executed action printed when the cycle reports one).
- UI: case-type badge, "Gap Priority Distribution" panel (renamed from
  "Investigation Debt"), "Thread Debt" panel, "The system noticed" panel
  (case overview); origin badges (👤 user / 🤖 external LLM / 🔧 ICE /
  📄 source) + "Proposed Claim-Hypothesis Edges" accept/reject review
  panel (hypothesis board); `[🤖 <source_llm>]` badge on MODEL_INFERENCE
  evidence rows (evidence graph); 6 matching `ui/api_client.py` methods.

### Demo script rewrite (fix-2-c, doc Part 2 §6.1, Feature 2E)

- `scripts/load_demo_case.py` rewritten to produce THREE cases in one run:
  **[debug]** checkout-latency incident (3 evidence, 3 hypotheses with
  `origin="user"`, 2 directly-authored claims + SUPPORTS/CONTRADICTS
  edges, 1 discriminating gap, 1 investigation thread, one full cycle —
  `case_type="debug"`, `required_confidence=0.85`);
  **[research]** cognitive-architecture survey (6 paper evidence with
  metadata, 2 hypotheses, 2 gaps with priorities 0.7/0.4, one cycle —
  `case_type="research"`, `required_confidence=0.0`);
  **[explore]** "Is my ICE project ready to ship?" pitch (1 user evidence
  + two `ingest_external_analysis` calls — claude-3.5-sonnet and gpt-4o —
  + 2 hypotheses with `origin="external_llm"` + `origin_detail`, one
  cycle — `case_type="explore"`, `required_confidence=0.0`). Prints each
  case_id, each case's summary counts, and a final three-case box; the
  docstring documents the case_type/required_confidence defaults each
  case demonstrates and the §0.7 canonical-script disposition.

### Homeostatic dead-code removal (fix-2-c, doc §11.1, Part 1's B.6)

- `engine/generate.py`: the dialogue-mode block (`reward_before` /
  `reward_after` / `violations` / `_last_homeostatic_reward` tracking /
  the `log_homeostatic_reward` call) and the `"homeostatic_delta"` return
  key are deleted; `_store_decision_trace` is now called with its None
  defaults (the decision_traces columns are nullable substrate).
  `engine/events.py::log_homeostatic_reward` KEPT as substrate (no
  in-repo callers remain — doc §12 shortcut #15); `psyche/state.py`
  definitions kept (`viability_zone_violated` is still used by
  `engine/tick.py`); `scripts/diversity_check.py` unaffected (its
  `.get()` guard means `mean_homeostatic_delta` now reports 0.0 — the
  metric was already of dubious value).

### Docs + env (fix-2-c, doc §13.6)

- README: new sections "Case types (debug · research · explore)" (with
  the §3.1 completion-criteria table + API/CLI create examples),
  "External LLM analysis (LLM-collaboration workflow)" (ingest surfaces,
  dedup behavior, the 0.4-weight placeholder note), "New endpoints quick
  reference (Part 2)", "NLI claim-hypothesis proposals (optional
  dependency)" (`pip install -e ".[nli]"`, proposed-edges workflow,
  fallback-to-manual note); migration inventory extended to 012a–017
  (incl. the dedupe_contradictions.sql precondition and the
  ice_migration_history behavior); CLI examples gain `--type`,
  `--auto-execute`, `add-llm`; deferred-to-Part-4 list added.
- AGENTS.md: fixes-document authority note; cycle.py canonical +
  investigation.py shim; EVENT_TYPES closed allow-list restated with the
  option-2 deviation; migration inventory 012a–017; new engineering
  contracts for case_type, the external-LLM 0.4 placeholder weight, and
  the NLI optional dependency.
- docs/PROV.md: `evidence.source_llm` mapped as the external-LLM actor
  (`prov:Agent` behind MODEL_INFERENCE evidence) and
  `hypotheses.origin`/`origin_detail` as agency attribution, plus
  proposed edges as candidate (not-yet-asserted) PROV statements.
- `.env.example` verified: `SCRAPER` / `SCRAPFLY_API_KEY` /
  `FIRECRAWL_API_KEY` (WebSearchExecutor gate) and `EXPERIMENT_SEED`
  (case_engine/llm.py) were already present; NLI needs no environment
  variables (local model, optional extra) — no additions required.

Verification for this pass: `py_compile` clean on all touched files and
the full tree; `.scratch/fix2b_verify.py` 86/86 (app-surface harness from
fix-2-b); `.scratch/fix2c_verify.py` (new) 97/97 — engine.generate import
+ TurnPipeline construction under stubs, the §11.1 grep invariants, the
demo script's module import + three loader functions, the §13.6
documentation keywords, and the `.env.example` keys;
`.scratch/final_import_sweep.py` 139 modules / 0 failures (grew with
fix-3's test files) and `.scratch/final_import_check.py` 470 imports / 0
errors; `.scratch/fix1_verify.py` 7/7 and `.scratch/fix2a_verify.py`
15/15 still green. Prior harnesses re-run green: ice2a (all PASS),
ice4a_verify (all — after ONE historical expectation was repaired:
its pre-Part-2 OpenAPI exact-equality check now asserts the historical
paths plus the 6 new Part 2 routes, with a comment), ice4a_live_http
(all), test_ice2c (all PASS), test_ice4c (58), verify_ice4b (132/132 —
after the same kind of repair: its `case create defaults` check expected
the pre-Part-2 CLI default 0.85, superseded by fix-2-b's sanctioned
`--required-confidence` default None). Both repairs are commented in the
harnesses. Live-Postgres acceptance (docker compose + verify_part2/
verify_part3 + §13.1–13.6 behavioral checks, incl. actually running the
demo script against a real database) remains blocked on the sandbox
environment as before.

## Merge note (ICE fixes doc Part 3 §0.10) — 2026-01-24

Final merge note per the fixes document's Part 3 §0.10 template,
consolidating the recovery scan (§0.1–§0.9) executed alongside the Part 1
merge and everything added since.

### Recovery scan — honest disposition

**The Oct 23 directory was never provided to this environment.** No
`hari-ice-revamp-oct23/` tree ever existed here to inventory, diff, or
recover files from — the document's §0.2 file-inventory, §0.3 presence
probe, and migration probe had nothing to probe beyond our Oct 22 base,
and `scripts/seed_demo.py` (§0.7) could not be recovered for the same
reason. What WAS available: the "hari ice fixes" document itself, whose
Part 1 carries the complete Oct 23 extraction pack (P1–P8, S1–S5,
R1–R19, the four manual patches, the disposition matrix) as verbatim
code, plus the OCT 23 EXTRACTION PACK appendix at its tail. The merge
therefore applied that pack's patches by hand onto the Oct 22 base —
the route the document itself prescribes for exactly this situation —
and **nothing else was available to recover**. Every claim below is
about that patch application, not about a scanned directory.

### Recovered from Oct 23 (missed by repomix scan)

- None — no Oct 23 directory existed on disk in this environment; there
  were no files to recover beyond what the document's extraction pack
  carries verbatim.

### Ported from Oct 23 → Oct 22 base

Applied from the document's verbatim patch code (per-patch detail in the
"Merge from Oct 23 (ICE fixes doc Part 1)" section above):

- **P1–P8** (P7 verified already landed in fin-0): evidence quality
  pipeline (`DEFAULT_QUALITY_SCORES`, `quality_score` + `metadata` on
  ingest and the API), hypothesis recompute (CONTRADICTS polarity
  branching + direct evidence→hypothesis edges), defensive jsonb
  parsing, `--falsify` CLI flag, `EvidenceResponse.metadata`, summary
  `next_action` via `select_next_best_action`, decision-attribution
  404, migration 012a + the `apply_ice_migrations` history table and
  sort key.
- **S1** (`max_actions` removed), **S2** (`events_emitted` via
  event_seq), **R6** (`_is_stagnant` includes `proposed`).
- **S3/S4/S5**: verified NOT shared by the Oct 22 base (deps-shim
  discipline, lazy package imports, conftest hygiene) — no change
  needed; the four manual patches are the by-hand forms of P3/S2/P6/P7.

### Renamed

- `case_engine/investigation.py` → `case_engine/cycle.py`; re-export
  shim retained (Option B, §0.6).

### Kept Oct 22 (Oct 23 version was a regression or equivalent)

- Everything else. The disposition matrix's "COPY from Oct 23" files
  (`case_engine/evidence.py`, `hypothesis.py`, `contradiction.py`,
  `app/routes/evidence.py`) were never imported — our versions carry
  fin-0/fin-1 fixes those copies lack, and the doc's own §0.4.1 merge
  rule (Oct 22 base + Oct 23 additions) was followed instead. Benchmark
  cases, the CI workflow (`.github/workflows/test.yml`), and the rest of
  the tree: Oct 22's, unchanged by the merge (no Oct 23 counterparts
  existed to compare against).

### Deleted

- Nothing. `scripts/seed_demo.py` (an Oct 23-only file per the document)
  was never recoverable — §0.7 disposition, **Case-1-equivalent outcome**:
  not ported, not created. `scripts/load_demo_case.py`, rewritten per
  Part 2 §6 as the three-case demo, is the **single canonical demo
  script**; no second seeder exists or will be added (§0.7.4).

### Added (per Part 1 and Part 2)

- `db/migrations/012a_safety_check.sql` (Part 1)
- `db/migrations/015_external_analysis_and_case_type.sql`,
  `016_contradiction_uniqueness.sql`, `017_proposed_edges.sql` (Part 2)
- `scripts/dedupe_contradictions.sql` (pre-016 dedupe runbook)
- `case_engine/endogenous.py` (Part 2 §5) and `case_engine/nli.py` (§10)
- `proposed_edges` review surface: routes + CLI + UI panel (§10.4–10.5)
- `tests/` extensions (fix-3, in progress at the time of this note)
- `verification/` dir (fix-4 scope — planned, not yet in tree)

### Deferred to Part 4 polish

- Embedding-based thread↔action matching (current: word-overlap, §12 #3)
- Calibrated external-LLM evidence weight (current 0.4 placeholder, #1)
- Real explore-mode decision ladder (current: documented stub, #6)
- `EXTERNAL_LLM` as its own source type (#8); case-type-specific
  decision symbols (#14)
- ADD proposed-edge event types to `EVENT_TYPES` + the spec's event
  table (§10.4 option 2 deviation is the interim state)
- Budget-bounded action generation instead of the 5/3/3 caps (#4)

## Verification pass (ICE fixes doc Part 4, sandbox scope) — 2026-01-24

Final verification + packaging pass (fix-4). Full artifact with pasted
outputs: `verification/2026-01-24/` (stage records) + `verification-report.md`
at the repo root (the doc's D.4 contract). Environment honestly sandbox:
no Docker, no Postgres server, no LLM keys, no browser — every
DB/LLM/UI/CI-dependent stage carries a `*_BLOCKED.md` with the exact
user-side commands and expected outputs; nothing is fabricated (no benchmark
numbers exist in this deliverable).

### verification/2026-01-24/ contents

- `00_baseline.md` — environment/versions (fastapi 0.128.0, pydantic 2.12.5,
  asyncpg 0.32.0, pgvector 0.5.1, httpx 0.28.1, pytest 9.0.2,
  pytest-asyncio 1.3.0; litellm/streamlit/transformers/testcontainers
  absent), 17-migration inventory, 35-key `.env.example` (values redacted),
  file inventory (233 files / 163 .py / 20 test files / 17 migrations),
  git state: not a HARI repo (workspace auto-snapshots only; `git init`
  instructions included).
- `01_static_compile.txt` — **Compiled: 163 files, 0 failed.**
- `01_imports.txt` — stubbed tree-wide sweep: **148 modules across 15
  packages, Failures: 0** (superset of doc §2.2's 41-module list; the sweep
  stubs asyncpg/pgvector even though installed, for determinism — the
  real-deps container smoke is user-side).
- `01_runtime_attrs.txt` — doc §2.3 + Part 2 extensions, 35/35 PASS
  (EventLogger methods, NBA surface + debt_bonus, action.py executor
  surface, claim prefilter, P2 recompute markers, case_type dispatchers,
  `enable_auto_execute` signature, cycle/shim identity,
  DEFAULT_QUALITY_SCORES + ingest_external_analysis, endogenous/nli entry
  points, 015 schema columns, NLI never-returns-non-None-without-model,
  executor mapping with fake Action objects).
- `05_unit_tests.txt` — `pytest tests/unit/ -m unit -v`: **153 passed in
  1.26s** (11 files); collection check: **239 tests collected**; honest
  integration/eval status: **86 skipped, 0 failed**.
- `02_migrations_BLOCKED.md`, `03_verify_part2_BLOCKED.md`,
  `04_verify_part3_BLOCKED.md`, `06_integration_tests_BLOCKED.md`,
  `07_eval_tests_BLOCKED.md`, `08_benchmark_BLOCKED.md`,
  `10_ui_walkthrough_BLOCKED.md`, `11_ci_BLOCKED.md` — exact user-side
  commands + expected outputs (docker compose build/up, psql table/column/
  constraint/index/vector checks, verify_part2 (8 steps) / verify_part3
  (15 steps) with "PASSED" tails, pytest -m integration/eval, the four
  benchmark JSON commands + resume-number extraction, UI manual checklist,
  git push + gh run checks).
- `09_cli_walkthrough_PARTIAL.md` — sandbox-verified argparse/dispatch
  round-trips (`--type`, `--falsify`, `--auto-execute`, `evidence add-llm`,
  bogus-type rejection — fix2b §d output pasted) + the full user-side
  command list.
- `12_recovery_scan.md` — dispositioned: Oct 23 directory never provided;
  nothing to scan; CHANGES.md §0.10 merge note is the record; user-side
  `comm -23` commands included for if the directory ever appears.
- `13_acceptance_checklist.md` — two-column aggregate over Part 1 §8.1–§8.8,
  Part 2 §13.1–§13.7, Part 3 §11.x, Part 4 §14.1 with the §8.1/§8.5 grep
  battery pasted verbatim; every item marked green-sandbox / green-static /
  blocked-user-side / waived. Two grep deviations documented honestly:
  `compute_priority_with_factors` in decision.py counts 3 (call + import +
  docstring; intent holds) and `add_exception_handler` in app/errors.py
  counts 1 (loop registration; live equivalence verified: 14 ERROR_MAP
  entries + 1 catch-all = 15 handlers registered).
- `harnesses/` — the two fix-4 harnesses copied into the artifact so it is
  self-contained (doc §16): `fix4_runtime_attrs.py` (§2.3 checks, 35/35)
  and `fix4_p2_polarity.py` (the P2.5 three-part polarity test on the real
  engine functions over a fake pool), plus a README with run commands and
  the stubbing rationale; both re-verified green from the artifact path.
- `SUMMARY.md` — doc §15.1 form adapted honestly: stages 0/1/5 green,
  6/7 blocked-skips-clean, 9 partial, 12 dispositioned, 13 aggregate;
  resume numbers section reads NOT AVAILABLE; the doc's known-limitations
  list verbatim; explicit "what this proves / does NOT prove" sections.

### Stage colors

0 baseline GREEN · 1 static GREEN · 2 migrations BLOCKED (static halves
verified) · 3 verify_part2 BLOCKED · 4 verify_part3 BLOCKED · 5 unit GREEN
(153/153) · 6 integration BLOCKED-skips-clean (81 skip, 0 fail) · 7 eval
BLOCKED-skips-clean (5 skip, 0 fail) · 8 benchmark BLOCKED (no numbers) ·
9 CLI PARTIAL · 10 UI BLOCKED · 11 CI BLOCKED · 12 recovery DISPOSITIONED ·
13 aggregate GREEN (as an honest record).

### fix-4 additions to the tree

- `verification-report.md` (repo root) — the doc's D.4 contract report:
  py_compile summary, stubbed import summary, 17-file migration inventory,
  verify_part2/3 NOT RUN statements + user commands, 239-test collection
  count, and the P2.5 polarity test executed on the real engine functions
  against a fake pool (SUPPORTS direct 0.5→0.627, CONTRADICTS direct
  0.5→0.373, claim CONTRADICTS 0.5→0.419 — Correction 6 inversion);
  declarations: NLI [implemented; inference not verified here], explore
  [stub], recovery [skipped: no Oct 23 directory], benchmark [not run].
- `verification/2026-01-24/` as itemized above.
- Contradiction self-pair guard (orchestrator, landed just before this
  pass): `detect_contradiction` returns None when `claim_a_id ==
  claim_b_id` (contradiction.py) — resolves fix-3's observation (c) that a
  self-pair produced a degenerate row; noted in
  `13_acceptance_checklist.md` under §13.3 idempotency.
- CI workflow verified current for the new suite: `.github/workflows/
  test.yml` runs exactly `pytest -m "unit"` / `-m "integration"` (with the
  pgvector/pg16 service container + DATABASE_URL) / `-m "eval"` — matching
  the shipped 20-file suite's markers. §0.8 disposition stands: only the
  Oct 22 workflow ever existed here; kept (no Oct 23 counterpart existed).
- Historical harness repairs (project precedent: fix-2-b repaired
  ice4a_live_http, fix-2-c repaired verify_ice4b + ice4a_verify) — all four
  breakages were caused by sanctioned earlier changes, none by current
  code, each repaired minimally with an explanatory comment:
  - `ice2a_verify.py`: CASES INSERT mapped to the migration-015 9-arg
    layout (was crashing with 'debug' landing in created_at); google.genai
    stub added (P6's get_case_summary → next_best_action → evidence →
    engine.memory import chain); case_created payload + summary-shape
    expectations gained `case_type` (Part 2 §1.4/§3.4); the summary
    next_action check now patches `select_next_best_action` (P6 replaced
    the inline "AS priority" SQL the old handler served).
  - `test_ice2c.py`: `_parse_insert` truncates at `ON CONFLICT` (§9.3's
    DO NOTHING suffix); the §9.2 existing-pair pre-check SELECT handled;
    the below-threshold/refinement checks moved to fresh claim pairs (the
    old pair was already contradicted, so the new idempotency pre-check
    correctly short-circuits instead of consulting the LLM).

### Final harness results (all from /home/z/my-project/hari)

| Harness | Result |
|---|---|
| `pytest tests/unit/ -m unit -q` | 153 passed in 1.26s |
| `pytest tests/ --collect-only -q` | 239 tests collected |
| `pytest tests/integration/ tests/eval/ -q` | 86 skipped, 0 failed |
| `.scratch/fix1_verify.py` | 7/7 PASS |
| `.scratch/fix2a_verify.py` | 15/15 PASS |
| `.scratch/fix2b_verify.py` | 86/86 PASS |
| `.scratch/fix2c_verify.py` | 97 passed, 0 failed |
| `.scratch/fix4_runtime_attrs.py` (new) | 35/35 PASS |
| `.scratch/fix4_p2_polarity.py` (new) | all PASS (P2.5 three-part) |
| `.scratch/ice2a_verify.py` | 58 PASS, 0 FAIL (after 4 documented repairs) |
| `.scratch/ice4a_verify.py` | ALL CHECKS PASSED |
| `.scratch/ice4a_live_http.py` | ALL LIVE-HTTP CHECKS PASSED |
| `.scratch/test_ice2c.py` | 118 PASS, 0 FAIL (after 2 documented repairs) |
| `.scratch/test_ice4c.py` | 58 checks PASSED |
| `.scratch/verify_ice4b.py` | PASS 132, FAIL 0 |
| `.scratch/final_import_check.py` | 0 errors (AST), 15 JSON OK, pyproject OK |
| `.scratch/final_import_sweep.py` | 148 modules, 0 failures |
| py_compile whole tree | 163 files, 0 failed |
| Doc §8.1/§8.5 grep battery | all green (pasted in 13_acceptance_checklist.md) |

### Packaging

`download/hari-ice-fixes-merged-oct24.zip` — the full `hari/` tree with
`hari/` as the zip root (same layout as the Oct 22 deliverable), excluding
`__pycache__/`, `*.pyc`, `.pytest_cache/`, and stray artifacts; includes
`verification/` (with the self-contained `harnesses/`) and
`verification-report.md` per doc §15.2. 293 zip entries = 254 files +
39 directories, ~522 KB; `unzip -t` clean; the zip's non-verification file
set diff-verified identical to the working tree.

### Remains user-side (commands in the BLOCKED files)

docker compose build/up; migrations + psql schema checks; verify_part2
(PASSED tail); verify_part3 (PASSED tail); integration + eval test runs;
benchmark smoke/full/baselines/ablation + resume numbers; CLI live
walkthrough; UI manual walkthrough; git init/push + CI green; (optional)
`pip install -e ".[nli]"` + NLI inference check.