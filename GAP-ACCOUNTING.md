# ICE × the Hari document — honest gap accounting

Date: 2026-11-17 · Scope: every major stream in "Hari ICE addition and
improvements.pdf" (10,000+ lines, re-read end-to-end for this document)
mapped against what the two implementation passes actually shipped, plus
the main concerns. Nothing here is padded: DEFERRED means it is not in
the code; PARTIAL means part of the contract is in the code.

The pass-1 motto the PDF's preamble demanded: *"Do not defer nothing that
has potential… implement even if wrong so I could cut or improve it
better. The intention or idea of mine in a code form survived in code
than being ignored is 100% my motto."* This document holds the work to
that standard.

---

## 1. Stream-by-stream status

### A. First-pass audit + P0–P7 roadmap — DONE (pass 1)

| Item | Status |
|---|---|
| P0 benchmark rebuild (no leakage, real metrics, calibration) | **DONE** — ran offline; honest floors recorded (top-1 0.2, fault-class 0.2, calibration 0.66) |
| P1 all 10 action executors real; outcome vocabulary; idempotent | **DONE** |
| P2 hypothesis induction (schema, QD archive, critique, tournament, offline path) | **DONE** — deterministic path tested; LLM path wired but never live-run (no credentials) |
| P3 action-conditioned EIG + discriminating-test planner | **DONE** |
| P4 timeline + causal layer (ACH, interventions) | **DONE** |
| P5 endogenous expansion + investigative workspace (portfolio, observe-only, refractory decay) | **DONE** |
| P6 domain integrations (RepoIntel provider, observability feeds) | **DEFERRED by design** — documented in the mechanism lab; the document itself ordered one proving ground first |
| P7 durable execution / Graphiti / GEPA / Inspect AI | **DEFERRED by design** — in the mechanism lab as the document's "what I would deliberately NOT do yet" |

### B. Second-pass audit register (falsification, replay, event
### consistency, cross-case, credibility, independence, prompts, tokens,
### explore ladder, consolidated cycle) — DONE (pass 1)

Every item in the audit's gap register is fixed and regression-tested;
see CHANGES.md and SELF-EVALUATION.md §2. The one deliberate residual:
legacy one-shot mutators still emit events post-commit (documented).

### C. The GOAL SYSTEM — the Evolving Project Model — **WAS THE BIGGEST
HOLE; NOW SHIPPED (pass 2, this session)**

Pass 1 reduced this stream to a `plan_items` checklist plus a "defer"
entry in the mechanism lab. That was a real deviation from the document
(the plan manager is a case-scoped task list, not an intent layer). Pass
2 ships the actual system:

| Document requirement | Status now |
|---|---|
| Intent nodes with the full object vocabulary (goal/need/actor/scenario/requirement/quality_objective/constraint/assumption/decision/alternative/risk/open_question/milestone) | **SHIPPED** — `case_engine/goals.py`, migration 021 (project_goals) |
| Goal clarity Levels 1–4; Level-1 vague intention is valid state | **SHIPPED** — clarity_level CHECK 1–4; Level-1 → status `exploring` |
| Provenance categories (developer_statement / developer_confirmed / ice_inference / ice_proposal / repo_observation); inference never silently becomes decision | **SHIPPED** — origin CHECKed; user decision PROMOTES to confirmed; reverse impossible |
| Intent versioned independently; revisions supersede, never delete | **SHIPPED** — supersede chains with forward pointers, version bumps |
| Typed relationships (refines, depends_on, constrains, motivated_by, conflicts_with, …) answering "which goals depend on this" | **SHIPPED** — goal_edges (9 relations) + `goal_dependency_breadth` |
| Implementation maturity tracked separately from verification (no_candidate→scaffolded→candidate→connected→tested→verified→runtime_observed) | **SHIPPED** — goal_progress; anything above scaffolded REQUIRES evidence ids |
| Intent discovery BEFORE a repository exists (initial intent profile with gaps) | **SHIPPED** — `intent_profile()` (8 slots, open slots = next questions) |
| Goal decomposition (GORE) | **PARTIAL** — `decompose_goal()` deterministic GORE-lite; proposals only (origin=ice_proposal); LLM phrasing path not wired |
| Continuous intention↔implementation reconciliation | **PARTIAL** — `reconcile_goals()` honest classification (aligned/accidental/intentional/temporary/unresolved; intentional requires user-recorded chain); keyword-coverage heuristic, LLM critique not wired; NO RepoIntel event feed yet (see E) |
| The three triggers (developer asks / code changes / background review) | **PARTIAL** — the developer-ask path exists via API/CLI; the code-change trigger awaits the RepoIntel event contract; background review exists only as the workspace tick |

### D. Research mode completing the research process — **STARTED FOR REAL
### (pass 2)**

The document's verdict was "research mode does not yet complete the
research process". Pass 1 left the research case type as a decision
ladder. Pass 2 ships the first working slice:

| Document requirement | Status now |
|---|---|
| Perspective discovery (STORM: "perspectives it did not initially consider") | **SHIPPED (lite)** — lenses derived from claim-type/evidence mix + 6 always-on defaults; unexplored ones flagged |
| Questions from perspectives → follow the evidence into gaps | **SHIPPED (lite)** — unanswered questions become REAL information gaps (dedup'd, capped, never auto-executed) |
| Distinguish established findings from plausible interpretations | **SHIPPED** — synthesis outline separates established / contested / interpreted, citations kept |
| Recognise when more research is not worthwhile | **SHIPPED (signal only)** — open-to-established ratio reported, not decided |
| Systematic source discovery / web research | **NOT BUILT** — needs the SEARCH executor to gain real web providers (P6 territory) |
| Fully grounded synthesis with citations and uncertainty | **PARTIAL** — outline is grounded in claim ids + support; prose synthesis generation not built |

### E. ICE × RepoIntel / continuous development guidance (unfinished
### projects) — **FOUNDATIONS SHIPPED, INTEGRATION NOT BUILT**

This is the stream your message asked about most directly ("ICE can be
used on unfinished projects"). The honest split:

- **NOW REAL**: the intent layer that stream needs exists (C above) — a
  developer can open a case, state a vague Level-1 idea, get an intent
  profile of what's unknown, decompose goals, record decisions with
  provenance, supersede them when direction changes, and reconcile
  intent against whatever evidence exists. This works with ZERO
  repository attached, exactly as the document demands ("ICE should be
  useful even before RepoIntel is connected").
- **NOT BUILT**: the RepoIntel provider contract (versioned event feed —
  RepositoryFilesChanged, SymbolGraphChanged, FindingUpdated…), the
  incremental parse/index pipeline (tree-sitter, Watchman), the
  editor-buffer/staleness distinction, impact analysis over trace links,
  the attention/notification policy for findings, and Hint/Explain/
  What-next surface. These are P6 integration work; they remain
  specified in the mechanism lab, not coded.

### F. Legacy Hari machinery (volition, pursuits, curiosity graph,
### prediction error, generativity) — **PORTED IN SPIRIT, NOT IN CODE**

The document's own ruling: "recover the underlying idea, not the entire
old implementation… port individual mechanisms behind case_engine
contracts". Pass 1 ported: PursuitRegistry semantics →
investigation_thread; attention competition + curiosity sources →
workspace/endogenous; prediction records → predictions.py (Brier,
calibration). `engine/volition_engine.py` and `engine/pursuits.py`
remain legacy islands, unwired into case mode. The generativity
estimator is still observational-only.

### G. Personal cognitive layer (global personal memory, preference /
### metapreference, PAHF feedback) — **FOUNDATIONS ONLY**

Workspace + endogenous + plan + goals are the substrate; the layer
itself (cross-case personal memory, metapreference records) is a
programme, not a pass. Mechanism lab carries the donor entries.

### H. The document's final consolidation programme — **NOT EXECUTED**

The document's own last recommendation was: consolidate all material →
full implementation audit → complete the six missing architecture
sections (identity/ownership, temporal/lifecycle, trust/security,
reliability/recovery, resource economics, capability evaluation) →
capability/dependency matrix → dependency-ordered roadmap; then the
12-dimension gap analysis (developmental, dynamical, ecological,
evolutionary…). Pass 1 skipped this and implemented P0–P5 instead
(defensible: the document's own roadmap ordering); pass 2 did not do it
either. **This consolidation work remains open and is now the highest-
value non-code deliverable.**

---

## 2. Main concerns (ranked)

1. **Core reasoning quality is still unproven at the top.** The honest
   benchmark floors (top-1 0.2, fault-class 0.2, calibration 0.66,
   wrong-leading-ACTed 2/5) are exactly what the rebuilt benchmark is
   supposed to expose — but the LLM path that should lift them has never
   run live (no provider credentials in this environment). The harness
   is proven; the reasoning is not yet.
2. **Calibration is poor (0.66 offline).** Token-overlap grounding
   overlinks generic support. The measuring instrument now exists; the
   fix (better grounding or LLM critique) does not.
3. **The document's consolidation programme is undone** (H above) — the
   six architecture sections, capability matrix and dependency-ordered
   roadmap. Without them, the next big phase risks building against an
   imaginary system again.
4. **RepoIntel integration is the missing half of the unfinished-
   projects story.** The intent layer exists; the live code-change feed,
   impact analysis and notification policy do not. Until they exist,
   "continuous development companion" is a data model, not a companion.
5. **The 5-case benchmark is tiny and synthetic.** No external
   validation (RCAEval's 735 cases, DeepResearch Bench) has been run.
6. **Two investigation pathways still exist** (legacy generate.py stages
   vs the consolidated cycle). The bridge exists but is default-off;
   drift risk remains until the legacy path is retired.
7. **Reconciliation/goal-evidence matching is keyword-based** in both
   plan.py and goals.py — honest and flagged, but a heuristic. It will
   misclassify on vocabulary drift until the LLM critique path lands.

---

## 3. What changed in this session (pass 2) — verification

- `case_engine/goals.py` (new, 828 lines) + `db/migrations/021_project_goals.sql`
  (3 tables) — the Evolving Project Model.
- `case_engine/research.py` (new, 456 lines) — STORM-lite perspectives,
  questions→gaps, tiered synthesis outline, one-call brief.
- 6 new event types in the allow-list; 17 new API routes
  (`app/routes/goals.py`); 2 new CLI groups (`ice goals`, `ice research`).
- mechanism_lab GOAP entry: defer → adapt.
- 14 new integration tests; suite 274 → **288 passed, 5 skipped**
  (LLM-gated eval), against real Postgres 16 + pgvector.
- Full chain re-verified end-to-end: suite, `verify_part2` PASS,
  `verify_part3` PASS, benchmark honest+legacy (numbers reproduce the
  recorded baselines exactly), API smoke ALL PASS (now exercising the
  goal lifecycle and research lifecycle over HTTP).

## 4. Where the deferred work is tracked

Every deferred item above has a mechanism-lab entry or a CHANGES.md
deviation note. The consolidation programme (concern 3) is the one with
no artifact yet — doing it is the recommended next step, and it is a
document task (a capability/dependency matrix + roadmap over everything
this file lists), not a code task.
