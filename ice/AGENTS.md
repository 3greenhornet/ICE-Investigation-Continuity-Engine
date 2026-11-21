# AGENTS.md — guardrails for AI implementing agents

One page. Read it before touching any code.

## Read the spec first

The authoritative specification is `upload/hari ICE revamp implementation.pdf`
(text extraction: `.scratch/ice_spec.txt`). Read **Parts 1 → 4 in order**
before writing or modifying anything. Part 1 is the architecture contract;
Parts 2–3 are module-by-module implementations; Part 4 is the interface and
operations layer. The embedded reviewer audit at the document tail is
*informative*, not normative — implemented gap fixes are listed in
`CHANGES.md`.

The follow-on consolidation document `upload/hari ice fixes.pdf`
(text extraction: `.scratch/ice_fixes.txt`) governs everything merged or
built after the Oct 22 base: its Part 1 patches (P1–P8, S1/S2, R6), Part 2
scope-broadening (case_type, external-LLM analysis, endogenous, NLI,
lifecycle/debt/idempotency), Part 3 ops, and Part 4 verification. When the
two documents disagree, the fixes document wins for the code it touched;
record deviations in `CHANGES.md`.

## The four-plane rules (non-negotiable)

1. **State mutations happen only inside `case_engine/` modules.** The
   projection plane (`app/`, `cli/`, `ui/`) and the substrate (`engine/`,
   `psyche/`) never write to the ICE tables directly.
2. **Every mutation emits exactly one `case_events` row** via
   `case_engine._emit_event`. Event types are a closed allow-list — adding a
   new event type requires a schema change (event payload table entry in the
   spec) **and** a spec table entry. Silent, unlisted event types are
   forbidden. Known deviation (fixes doc Part 2 §10.4, option 2):
   proposed-edge **accept/reject log at INFO level only** — they emit no
   `case_events` rows because `proposed_edge_accepted/rejected` are not in
   `EVENT_TYPES`; adding them is deferred to Part 4 polish. Every other
   mutation still emits exactly one event (contradiction re-detections
   return the existing row and emit nothing).
3. **No plane calls upward.** Projection calls control; control touches state;
   state emits events. Never let `case_engine` import from `app/`, `cli/`, or
   `ui/`, and never let the substrate reach into `case_engine` internals.
4. **Postgres is the only truth.** Nothing enters state except through
   `case_engine/evidence.py` ingestion. UIs are stateless projections.

## Do not "improve" the architecture

- **Do not modify `case_engine/__init__.py`** without reading spec Part 1 §3.4
  (`_emit_event`, the event allow-list, the `CaseEvent` dataclass).
- **Do not weaken the explicit `_row_to_*` column mapping.** Rows are mapped
  by name, never by position or `SELECT *` — this is the anti-schema-drift
  contract (spec Part 2 §0.8).
- **Do not merge modules.** The spec's public module split is the API
  surface; renames/merges break the API, CLI, tests, and verify scripts.
  One rename is already done deliberately: `case_engine/investigation.py`
  → `case_engine/cycle.py` (canonical; the old path remains as a re-export
  shim — import from `cycle`). Part 2 additions `endogenous.py` and
  `nli.py` are part of the same surface.
- **Do not enable legacy flags.** `ENABLE_DETERMINISTIC_CASCADES`, `ENABLE_GRACE`,
  `ENABLE_VOLITION_CAUSAL`, `ENABLE_REFLEXION`, `ENABLE_BROADCAST_FEEDBACK`,
  `ENABLE_PRM`, `ENABLE_LECTURER_PENALTY`, `ENABLE_SEMANTIC_REFRACTORY`,
  `ENABLE_AUTHORITY_CAUSAL` stay **false**. `STERILE_MODE` stays **true**.
- **Do not delete legacy files.** `engine/`, `psyche/`, `models/`,
  `providers/` are historical context and remain importable because
  dialogue-mode `TurnPipeline` backward compatibility requires them.

## Engineering contracts

- **SQL:** asyncpg `$1` placeholders, with explicit `::jsonb` / `::text[]`
  casts where needed. Every case-scoped query filters `case_id = $1`
  (failure mode F3). Transactions for version-race paths (F17).
- **Time:** timezone-aware UTC only. Naive datetimes are a bug (audit gap #40);
  promote, never store naive.
- **Scores must expose factor breakdowns.** Any scoring function (e.g.
  `compute_priority`, quality scores) returns/records its factors — silent
  scoring is forbidden (spec Part 3 §0 rule 11).
- **Confidence is not probability.** Never present it as calibrated
  (failure mode F11).
- **Env config:** read once at process start (spec §9.2). Add new variables to
  `.env.example` *and* the spec's §21.4 index.
- **Migrations:** new tables/columns go in a new numbered
  `db/migrations/*.sql` file; never edit shipped migrations. Current ICE
  inventory: `012a, 012, 013, 014, 015, 016, 017` (015 adds
  source_llm/origin/origin_detail/case_type; 016 adds contradiction
  uniqueness — on dirty databases run `scripts/dedupe_contradictions.sql`
  BEFORE 016; 017 adds proposed_edges). `scripts/apply_ice_migrations.py`
  is idempotent via table-exists checks plus its `ice_migration_history`
  table (which also covers ALTER-only files like 015/016).
- **case_type is a per-case column, not an env flag.** `decide()` and
  `generate_actions()` branch on `cases.case_type` (`debug` / `research` /
  `explore`); `required_confidence` defaults per type (0.85 debug, 0.0
  research/explore — an explicit value always wins). Explore's decision
  ladder is a documented stub (always INVESTIGATE); do not "complete" it
  without spec authority.
- **External-LLM evidence is deliberately discounted.** MODEL_INFERENCE
  evidence with `source_llm` set weighs 0.4 — a documented **placeholder**,
  not a calibrated value (fixes doc Part 2 §12 shortcut #1); without
  `source_llm` it weighs 0.0 (self-confirmation guard). Do not raise the
  weight without calibration data.
- **The NLI classifier (`case_engine/nli.py`) is an optional dependency**
  (`pip install -e ".[nli]"` — transformers + torch; NOT installed by
  default, no env vars needed). Without it,
  `classify_claim_against_hypothesis` returns `None` and no proposed edges
  are created — the manual claim→hypothesis paths are the fallback. Never
  let NLI write the evidence graph directly: proposals land in
  `proposed_edges` pending human accept/reject.

## Acceptance

The implementation is correct when `scripts/verify_part2.py` and
`scripts/verify_part3.py` pass against a real Postgres + pgvector database
(see `docker-compose.yml`), with all legacy flags false and `STERILE_MODE=true`.
When you are asked to fill gaps, prefer the spec's own text over intuition,
record deviations in `CHANGES.md`, and append what you did to
`/home/z/my-project/worklog.md`.

## Two development lanes (Hari ICE "addition and improvements" pass)

The improvements document itself flagged that these guardrails, written for
maintenance, "are not necessarily compatible with your current objective"
of experimenting with new cognitive arrangements. Resolution — two lanes:

**Stable lane (unchanged):** everything above still holds for the existing
modules. The four-plane rules, `_emit_event`, the event allow-list,
`_row_to_*` mapping, and legacy flag policy are preserved. The verify
scripts still define acceptance (they now PASS offline — the deterministic
fallbacks are engine capabilities, not spec violations; see CHANGES.md).

**Experimental lane (this pass, see CHANGES.md):** the new reasoning
modules (induction, predictions, causal/ACH, timeline, workspace, plan,
retrieval, introspection/trajectory/diagnostics, mechanism lab) live
BEHIND the same contracts — they emit only allow-listed events (9
additions, each with a §7-style payload table in CHANGES.md), never write
from outside case_engine, and default OFF in the cycle (`enable_induction`
/ `enable_workspace` / `ICE_CONSOLIDATED_CYCLE`). Evaluating their
contribution independently is the point: the rebuilt no-leakage benchmark
is the measuring instrument.