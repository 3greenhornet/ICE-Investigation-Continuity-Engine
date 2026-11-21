# ICE — Investigation Continuity Engine

ICE is a persistent reasoning system that maintains evidence, competing
hypotheses, contradictions, and unresolved questions across sessions, and
selects the next best investigation step using attention and
uncertainty-guided action selection.

It was built by **semantic inversion of the Hari cognitive-architecture
substrate**: the same memory, attention, curiosity, and tick machinery, aimed
at investigations instead of inner life.

- **Primary output:** structured case state — *not* chat text. Natural
  language is a projection, not the product.
- **Boundary:** ICE does not act autonomously. All `EXTERNAL_SIDE_EFFECT`
  actions require human approval.
- **Non-goals:** it does not simulate consciousness, does not claim calibrated
  probabilities, and does not replace human judgment.


---

## Hari ICE "Addition and Improvements" pass — what changed

This pass closed the reasoning loop the audits identified
("infrastructure-rich, reasoning-loop-thin") while preserving the four-plane
architecture. Everything below is documented in detail in `CHANGES.md`;
the honest benchmark baselines live in `benchmark/results/`.

**The loop now actually loops:**

| Audit gap | Now |
|---|---|
| The cycle never generated hypotheses | `case_engine/induction.py` — generate → diversify (quality-diversity archive) → critique → tournament, with a deterministic offline path + grounding evidence links |
| 7 of 10 action types were no-ops | All 10 have real executors; `actions.outcome` ∈ EVIDENCE_PRODUCED / INFORMATIVE_NO_EVIDENCE / UNINFORMATIVE / UNSUPPORTED — no more completed-on-empty |
| EIG was not EIG | True action-conditioned `expected_information_gain` (outcome models, Bayesian posteriors); old heuristic renamed `uncertainty_priority_heuristic` |
| Counterfactual replay wasn't counterfactual | `replay_without_evidence` REBUILDS confidences from remaining links |
| Contradiction ≠ explanatory reasoning | `timeline.py` (state-change windows) + `causal.py` (reviewed causal edges, ACH matrix) |
| Endogenous = ageing rules | 5 new curiosity sources + `workspace.py` attention portfolio with observe-only |
| No evidence retrieval API | `retrieval.py` — hybrid search, contrastive (counterevidence-hunting), associative, cross-case |
| Benchmark leaked answers | No-leakage runner; pair-matched contradictions; real decision correctness; calibration; ran offline with real numbers |

**First honest benchmark numbers** (deterministic offline mode, no LLM —
the LLM path is the quality path, this is the floor): top-1 statement
accuracy 0.2 / fault-class accuracy 0.2, contradiction precision 0.5 /
recall 0.2, calibration error 0.66, wrong-leading-ACTed 2/5 (a failure
mode the old proxy metric could not see). Full reports:
`benchmark/results/offline_*.json`.

**New surfaces:** `ice reasoning <induce|cycle|workspace|ach|timeline|
search|contrastive|trajectory|introspection|predict|calibrate|plan|
mechanisms>` (CLI), `/api/v1/...` reasoning routes (26 new endpoints),
`ice benchmark run --seed-from-resolution --execute-actions`.

**Offline capability:** claim extraction, contradiction detection,
duplicate detection and thread matching all have deterministic fallbacks
(provenance-marked) — the engine, the test suite, the verify scripts and
the benchmark now run with zero credentials. `python
scripts/run_benchmark_offline.py` reproduces the baselines.

**Goal system — the Evolving Project Model** (`case_engine/goals.py`,
migration 021). ICE now represents project INTENTION as a first-class
typed graph, from the first vague conversation (clarity Level 1) to a
defined engineering objective (Level 4):

* intent nodes with the document's full kind vocabulary (goal / need /
  actor / scenario / requirement / quality_objective / constraint /
  assumption / decision / alternative / risk / open_question / milestone);
* ORIGIN preserves the epistemic category — developer_statement /
  developer_confirmed / ice_inference / ice_proposal / repo_observation —
  and a user decision PROMOTES an inference to confirmed; the reverse
  never happens silently;
* revision = supersede chain: old intents stay visible, crossed out,
  never deleted;
* maturity ladder per goal (no_candidate → scaffolded → candidate →
  connected → tested → verified → runtime_observed) where any claim
  above `scaffolded` REQUIRES evidence ids — "a class existing does not
  prove a feature works";
* `intent-profile` — ICE's current understanding of a project INCLUDING
  its gaps, before any repository exists;
* `decompose` — GORE-lite goal decomposition that PROPOSES open
  questions (origin=ice_proposal), never decides;
* `reconcile` — intent-vs-implementation classification (aligned /
  accidental_divergence / intentional_evolution / temporary_deviation /
  unresolved / intent_questioned) where intentional evolution requires a
  user-recorded chain; keyword overlap alone can never produce it.

**Research mode completes (the start of) the research PROCESS**
(`case_engine/research.py`): STORM-lite perspective discovery (lenses
derived from the actual claim-type/evidence mix PLUS defaults nobody had
to think of, with unexplored ones flagged), perspective-guided questions
that evidence does NOT answer become REAL information gaps (dedup'd,
never auto-executed), and a synthesis outline that separates
established / contested / interpreted claims with citations and a
marginal-value signal instead of deciding for you. `research_brief()`
runs the whole pass.

**New surfaces for both:** `ice goals <add|list|show|status|supersede|
decompose|maturity|intent-profile|reconcile>` and `ice research
<perspectives|questions|outline|brief>` (CLI), 17 new `/api/v1` routes
(`POST /cases/{id}/goals`, `/goals/{id}/decompose`,
`GET /cases/{id}/intent-profile`, `GET /cases/{id}/goal-reconciliation`,
`GET /cases/{id}/perspectives`, `POST /cases/{id}/research-questions`,
`GET /cases/{id}/research-outline`, …).

## Architecture — four planes

| Plane | Implemented by | Rule |
|---|---|---|
| **State** | PostgreSQL + pgvector (`db/migrations/012a–017`) | Authoritative. The `cases` row is the object that survives sessions; `case_events` is the immutable timeline. |
| **Control** | `case_engine/` + the legacy substrate (`engine/`, `psyche/`, `models/`) | All state mutations go through `case_engine` modules; every mutation emits exactly one `case_events` row. No plane calls upward. |
| **Environment / Evidence** | `case_engine/evidence.py` (documents, URLs, human input, tool results) | Ingest boundary: **nothing enters state without `ingest_evidence`**. |
| **Projection** | `app/` (FastAPI), `cli/`, `ui/` (Streamlit) | Never owns truth — the UI is stateless and reads from the API on each render. |

## Repository layout

| Path | What it is |
|---|---|
| `case_engine/` | The ICE control plane (case, evidence, claim, hypothesis, contradiction, information_gap, investigation_thread, evidence_graph, provenance, action, next_best_action, decision, falsification, replay, **cycle** (renamed from `investigation`, which remains as a re-export shim), consistency, metrics, action_value_estimator, llm, **endogenous**, **nli**, schemas, exceptions, `__init__` with `_emit_event`) |
| `app/` | FastAPI service: `main.py`, `schemas.py`, `deps.py`, `errors.py` + 11 route modules |
| `cli/` | `ice` command-line interface (case, evidence, hypothesis, contradiction, gap, thread, action, decision, investigate, export, benchmark groups) |
| `ui/` | Streamlit app (`streamlit_app.py`, `api_client.py`, 5 screens) |
| `benchmark/` | Runner, 5+ metrics (incl. Investigation Efficiency, Evidence Utilization), baselines B1–B6, ablation runner, 5 case fixtures |
| `db/migrations/` | `002–011` legacy Hari schema; `012a–017` ICE schema (14 tables) |
| `engine/`, `psyche/`, `models/`, `providers/`, `db/`, `scripts/` | **LEGACY SUBSTRATE — kept as historical context.** Participates only as substrate for case mode: memory embeddings, attention, curiosity graph, tick runner, event logging. |
| `conftest.py`, `pytest.ini`, `.github/workflows/test.yml`, `tests/` | Test harness (fixtures/markers/CI; the Part 3 §5 suite is landing in `tests/unit|integration|eval/` — see `tests/README.md`) |
| `Dockerfile`, `Dockerfile.ui`, `docker-compose.yml`, `.env.example` | Deployment (spec §19) |
| `docs/PROV.md` | W3C PROV mapping of ICE entities |

## Quickstart

```bash
cp .env.example .env      # Windows: Copy-Item .env.example .env
# edit .env; add at least one provider key for LLM-backed flows
docker compose up -d --build
docker compose ps                             # postgres healthy, api + ui running
curl http://localhost:8000/api/v1/health/ready  # {"status":"ok","db":"ok","llm":"ok"}
open http://localhost:8501                    # Streamlit UI
```

### Configuration

`.env.example` is the canonical configuration template. Copy it to `.env`;
Docker Compose reads it automatically, while CLI/scripts that call
`load_dotenv()` read it from the project directory. The important settings are:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL + pgvector connection for the API, CLI, and benchmarks |
| `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY` | Provider credentials; one is enough for LLM-backed flows |
| `LLM_FALLBACK_CHAIN` | Optional ordered provider/model fallback list |
| `ENABLE_FALSIFICATION_LOOP` | Enables the LLM-backed falsification stage; default `false` |
| `ENABLE_NEXT_BEST_EVIDENCE` | Enables action-conditioned evidence selection |
| `SCRAPER` plus its API key | URL evidence backend (`scrapfly` or `firecrawl`) |
| `CORS_ORIGINS` and `API_BASE_URL` | API/UI connection settings |

Do not put real credentials in `.env.example`, source files, benchmark
fixtures, or committed logs. The offline claim/contradiction fallbacks and
the Serendipity engine do not require provider credentials.

### Local development without Docker

Install the package and development tools, then point `DATABASE_URL` at a
running PostgreSQL 16 instance with the `vector` extension:

```powershell
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python scripts/apply_ice_migrations.py
python -m uvicorn app.main:app --reload
```

Run the fast checks:

```powershell
python -m pytest tests/unit -q
Set-Location serendipity-engine
python -m unittest discover -s tests -q
```

The API and case CLI require PostgreSQL. Unit tests and the standalone
Serendipity engine can run without a database; integration tests skip when
PostgreSQL/testcontainers are unavailable.

### Standalone Serendipity engine

The optional `serendipity-engine/` package provides exploration suggestions
with safety filters, diversity controls, feedback attribution, and SQLite
persistence. It does not modify ICE state unless the
`ice_adapter.IceSerendipityAdapter` is explicitly used.

```powershell
Set-Location serendipity-engine
python -m unittest discover -s tests -q
python demo/demo_chat.py
```

For durable use, close the engine with `engine.close()` or use it as a
context manager. The adapter can be refreshed with `adapter.sync()` after the
CuriosityGraph changes.

The api container applies the ICE migrations (012a–017) automatically on
startup via `scripts/apply_ice_migrations.py` (idempotent — skips tables
that already exist and files recorded in its `ice_migration_history`
table, so ALTER-only migrations such as 015/016 also apply exactly once).
The compose file mounts `db/migrations` read-only for reference only. For
non-compose deployments, run that script manually, or `psql` the files in
order. Legacy migrations (002–011) have their own runner (see
*Migrations* below).

### CLI examples

```bash
python -m cli.cli case create --title "Checkout latency spike" --objective "Find root cause"   # debug (default)
python -m cli.cli case create --title "Cognitive architecture survey" --objective "Synthesize" --type research
python -m cli.cli investigate run <case_id>       # one investigation cycle
python -m cli.cli investigate run <case_id> --falsify        # + falsification loop
python -m cli.cli investigate run <case_id> --auto-execute   # + approve & execute the top action
python -m cli.cli evidence add-llm <case_id> --llm claude-3.5-sonnet \
    --prompt "What is the root cause?" --response "Likely the connection pool."
python -m cli.cli export json <case_id>           # also: export markdown | graph | trace
```

The CLI is also installed as the `ice` console command
(`ice case list`, `ice investigate run <case_id>`, …). For fully populated
demo cases — one per case type (debug latency incident, research survey,
explore pitch with ingested external-LLM analyses):

```bash
python scripts/load_demo_case.py   # prints three case_ids + summary counts
```

Verification scripts (run against a live DB):

```bash
python scripts/verify_part2.py    # case/evidence/claim/hypothesis paths
python scripts/verify_part3.py    # contradiction → decision → replay paths
```

## Case types (debug · research · explore)

Every case carries a `case_type` (migration 015; default `debug`). The type
branches `decide()` and `generate_actions()` — same tables, same events,
different ladders:

| Type | "Done" means | `decide()` | `generate_actions()` sources | `required_confidence` default |
|---|---|---|---|---|
| `debug` | Leading hypothesis reaches `required_confidence` with no decision-critical contradictions | The 6-rule confidence ladder | Gaps, falsification, contradictions, stagnation | 0.85 |
| `research` | No active contradictions AND no unresolved gap above priority 0.5 | No-hypotheses → active-contradictions → high-priority-gaps (> 0.5) → ready-to-synthesize ACT | Gaps (SEARCH), contradictions (COMPARE), uncited/aged material (REVISIT_HYPOTHESIS) | 0.0 |
| `explore` | The user marks the case resolved manually | Four-rule exploratory ladder; never auto-acts | Debug sources + endogenous surfacing | 0.0 |

Create examples:

```bash
# CLI
ice case create --title "Checkout latency spike" --objective "Find root cause"                 # debug
ice case create --title "Cognitive architecture survey" --objective "Synthesize" --type research
ice case create --title "Is my project ready to ship?" --objective "Decide" --type explore
```

```bash
# API
curl -X POST http://localhost:8000/api/v1/cases \
  -H 'Content-Type: application/json' \
  -d '{"title": "Cognitive architecture survey", "objective": "Synthesize mechanisms", "case_type": "research"}'
# required_confidence may be omitted — the engine defaults it per type
# (0.85 debug / 0.0 research & explore); an explicit value always wins.
curl 'http://localhost:8000/api/v1/cases?case_type=research'   # list filter
```

The cycle itself takes two more opt-in switches: `--falsify` (falsification
loop on the top hypothesis) and `--auto-execute` (approve + execute the
top-priority action at the end of the cycle — the operator stays in the
loop unless they opt in). The API equivalents are
`POST /cases/{id}/investigate` with `enable_falsification` /
`enable_auto_execute`.

## External LLM analysis (LLM-collaboration workflow)

Analyses produced by *other* LLMs (Claude, GPT, …) can be filed as
evidence so they feed the confidence loop without being trusted like
human observations. `ingest_external_analysis`
(`case_engine/evidence.py`, Feature 2A) creates a HUMAN source (the user
brought it in), one `MODEL_INFERENCE` evidence row with `source_llm` set,
and metadata recording the prompt, the response, and any conversation
context. Every surface is wired:

```python
# engine (case_engine/evidence.py)
await ingest_external_analysis(
    case_id, llm_name="claude-3.5-sonnet",
    prompt_used="…", analysis_text="…", conversation_context="…",
)
```

```bash
# API — POST /cases/{case_id}/evidence/from-llm
curl -X POST http://localhost:8000/api/v1/cases/<id>/evidence/from-llm \
  -H 'Content-Type: application/json' \
  -d '{"llm_name": "claude-3.5-sonnet", "prompt_used": "…", "analysis_text": "…"}'

# CLI
ice evidence add-llm <case_id> --llm claude-3.5-sonnet --prompt "…" --response "…"
```

- **Dedup:** re-filing the same `(llm_name, prompt_used)` pair returns the
  EXISTING evidence (SHA-256 hash on the lowercased pair, checked before
  anything is inserted) — no orphaned source rows, no double counting.
- **Weighting:** `_evidence_weight` scores external-LLM evidence **0.4** —
  a **placeholder** round number between 0.0 (ICE's own inference, banned
  for self-confirmation) and 1.0 (human observation), not a calibrated
  value (honesty note, doc Part 2 §12 shortcut #1; calibration deferred to
  Part 4). MODEL_INFERENCE evidence without `source_llm` still scores 0.0.
- **Provenance:** hypotheses can attribute the external LLM's agency via
  `origin="external_llm"` + free-form `origin_detail` (e.g.
  `"aggregated from claude-3.5-sonnet and gpt-4o"`); the UI renders
  origin and `[🤖 source_llm]` badges.

## New endpoints quick reference (Part 2)

| Method & path | What it returns |
|---|---|
| `POST /cases/{case_id}/evidence/from-llm` | Ingest an external LLM analysis as MODEL_INFERENCE evidence (201; `(llm, prompt)` dedup) |
| `GET /cases/{case_id}/suggestions` | Endogenous "the system noticed" candidates: aged threads, uncited evidence, stuck hypotheses, aged contradictions |
| `GET /cases/{case_id}/threads/debt` | Active investigation threads with their computed debt / age / strength |
| `GET /cases/{case_id}/proposed-edges` | Pending NLI-proposed claim→hypothesis edges, newest first |
| `POST /proposed-edges/{edge_id}/accept` | Accept: adds the edge to the evidence graph (weight = proposal confidence) and resolves the proposal |
| `POST /proposed-edges/{edge_id}/reject` | Reject: resolves the proposal without touching the graph |

(All under the `/api/v1` prefix. `GET /cases` gained a `?case_type=`
filter; `POST /cases` gained `case_type`; the investigate route gained
`enable_auto_execute` and its result now carries `lifecycle_changes` +
`auto_executed_action`.)

## NLI claim-hypothesis proposals (optional dependency)

`case_engine/nli.py` classifies extracted claims against existing
hypotheses with RoBERTa-large-MNLI and files SUPPORTS/CONTRADICTS
suggestions into the `proposed_edges` table (migration 017) instead of
writing the evidence graph directly — nothing enters the confidence loop
without your confirmation.

```bash
pip install -e ".[nli]"        # transformers + torch; NOT installed by default
```

Workflow: extract claims (CLI `claim extract` or any ingest that extracts)
→ the classifier files proposals → review them in the UI's *Proposed
Claim-Hypothesis Edges* panel (or `GET /cases/{id}/proposed-edges`) →
accept/reject. Accepting adds the edge with the proposal's confidence as
its weight; rejecting leaves the graph untouched.

**Fallback to manual:** without the `nli` extra the classifier returns
`None` for every pair (never raises), so no proposals are created — the
manual paths are unchanged: link evidence to claims with
`ice claim link`, seed a hypothesis's `supporting_claim_ids` at creation,
or write claim→hypothesis edges directly with the engine's `add_edge`
(exactly what `scripts/load_demo_case.py` does). NLI needs no API keys or
environment variables; the model is local. Honest note (doc §12 shortcut
#13): surface-text matching can misclassify inferential relationships;
the flat 0.8 proposal confidence discards the real probability
(shortcut #14).

## Migrations

| Files | Schema | Notes |
|---|---|---|
| `002–011` | Legacy Hari (memories, pursuits, decision traces, retrieval, sessions, edge weights) | SQL files; `scripts/migrate_all.py` applies the legacy INLINE schema (memory-consolidation + self_beliefs) — for the full legacy SQL set run the files via `psql` in order. Required by the legacy substrate (dialogue-mode `TurnPipeline`). |
| `012a_safety_check.sql` | ICE: pre-flight guard (no tables) | Aborts with `RAISE EXCEPTION` when the legacy `hypotheses`/`contradictions` tables still hold rows, before 012 drops them. |
| `012_case_model.sql` | ICE: `cases, sources, evidence, claims, hypotheses, hypothesis_versions, contradictions, information_gaps, investigation_threads, actions, decisions` (11 tables) | Applied after 012a; all `TIMESTAMPTZ`; claims carry a lifecycle `status` column; drops the (verified-empty) legacy tables whose names collide. |
| `013_evidence_graph.sql` | ICE: `evidence_graph_edges` (typed relations, 12th table) | |
| `014_case_events.sql` | ICE: `case_events` (event sourcing, `UNIQUE(case_id, event_seq)`, 13th table) | |
| `015_external_analysis_and_case_type.sql` | ICE: `evidence.source_llm` (+partial index), `hypotheses.origin`/`origin_detail` (CHECKed), `cases.case_type` (CHECKed + `idx_cases_type_status`) | ALTER-only; idempotent (`IF NOT EXISTS` throughout) |
| `016_contradiction_uniqueness.sql` | ICE: generated `contradictions.claim_pair_key` + `uq_contradictions_claim_pair`/`uq_contradictions_direct` + `idx_contradictions_pair` | **Precondition:** on a DB that already holds duplicate contradictions, run `scripts/dedupe_contradictions.sql` BEFORE 016, or the unique-constraint creation fails |
| `017_proposed_edges.sql` | ICE: `proposed_edges` (NLI proposals pending review, `UNIQUE(case_id, from_node, to_node, relation)`) | 14th table; accept/reject emit INFO logs only (no new event types — documented deviation, doc §10.4 option 2) |

Order matters within each group. 012a–017 are applied automatically by the
root `conftest.py` fixture in tests, and in deployment by the api container's
startup command (`scripts/apply_ice_migrations.py`, idempotent — safe on
every restart; letter-suffixed patch files such as `012a` sort immediately
BEFORE their base number, so the safety check always precedes the drops;
the `ice_migration_history` table records ALTER-only files such as 015/016
exactly once). The Postgres `docker-entrypoint-initdb.d` mount does NOT
execute SQL inside subdirectories, which is why the startup script exists.

## Semantic inversion notes (what happened to the legacy files)

| Hari (legacy) | ICE (new) |
|---|---|
| `engine/pursuits.py` PursuitRegistry | `case_engine/investigation_thread.py` InvestigationThreadRegistry |
| psyche GenerativityEstimator | `case_engine/next_best_action.py` (re-exported by `action_value_estimator.py`) |
| `engine/volition_engine.py` | `case_engine/action.py` |
| Prediction error | split: `compute_investigation_surprise` in `engine/prediction.py` + conversation surprise |

**Retired from the product path, kept in-repo as historical context**
(`behavior/`, `expression/`, `reflection_edit/`, `quarantine/`,
`social_cognition/`, `relational_manager/`, `identity/`): dialogue-mode
`TurnPipeline` backward compatibility still imports them, so they are not
deleted. Psyche cascades, grace, and fallback are disabled via flags (all
legacy flags default **false**), and `STERILE_MODE` now defaults **true**
(contamination quarantine active in the ICE product path).

## The 3 P0 fixes

- **A — cross-session contamination:** `engine/promotions.py` filters by
  session (and case in case mode) before promoting thoughts to hypotheses.
- **B — tick turn numbers:** `engine/tick.py` turn counter no longer collides
  with interactive turns; ticks carry `is_tick` and `turn_count` bookkeeping
  (fixes `turn_number > 100000` corruption, failure mode F4).
- **C — dead drive-boost removal:** `engine/memory.py` drops the dead
  `drive_boost` code path.

## Feature flags

ICE (see `.env.example` for the full reference):

| Flag | Default | Effect |
|---|---|---|
| `ENABLE_CONTRADICTIONS` | true | Contradiction detection pipeline |
| `ENABLE_HYPOTHESIS_VERSIONING` | true | Immutable hypothesis versions |
| `ENABLE_FALSIFICATION_LOOP` | false | Two-stage falsification proposals |
| `ENABLE_NEXT_BEST_EVIDENCE` | true | EIG-guided next-best-action scoring |
| `ENABLE_REPLAY` | true | Event-sourced replay/counterfactual |
| `ENABLE_BACKGROUND_CASE_REVIEW` | true | Background tick case review |
| `ENABLE_SOURCE_INDEPENDENCE` | true | Lineage-based independence scoring |
| `STERILE_MODE` | true | Legacy-substrate contamination quarantine |

`ENABLE_CONTRADICTIONS`, `ENABLE_HYPOTHESIS_VERSIONING`, `ENABLE_REPLAY`
and `ENABLE_SOURCE_INDEPENDENCE` are read **at call time**, so tests and
the B4/B5 benchmark baselines can toggle them after import.
`ENABLE_FALSIFICATION_LOOP` and `ENABLE_NEXT_BEST_EVIDENCE` are read at
import time (spec §0 convention 13) — like `case_engine.llm`'s
`ACTIVE_FALLBACK_MODELS`, API keys and these flags must be present in the
environment **before** `case_engine` is first imported.

Legacy Hari flags — `ENABLE_DETERMINISTIC_CASCADES`, `ENABLE_GRACE`,
`ENABLE_VOLITION_CAUSAL`, `ENABLE_REFLEXION`, `ENABLE_BROADCAST_FEEDBACK`,
`ENABLE_PRM`, `ENABLE_LECTURER_PENALTY`, `ENABLE_SEMANTIC_REFRACTORY`,
`ENABLE_AUTHORITY_CAUSAL` — **all default false** and must stay false in the
ICE product path.

## Failure modes

F1–F20 (hallucinated claims, model self-confirmation, cross-case
contamination, LLM outage + circuit breaker, event-sequence races, …) are
documented in the implementing code's docstrings and in the spec's §20 table;
`docs/PROV.md` maps provenance concepts. `case_engine/consistency.py`
(`validate_case`) and `case_engine/metrics.py` provide runtime integrity and
efficiency checks.

## Deferred — Phase 2+ (NOT implemented)

Graphiti temporal knowledge graph · DBOS/Temporal durable execution · MCP
boundary · Langfuse observability · omega-audit · DeepEval integration
(`DEEPEVAL_ENABLED` is a placeholder) · idempotency keys on POST endpoints ·
schema doc generation · prompt centralization. These appear in
`.env.example` under "DEFERRED" for forward-compatibility only.

Deferred to Part 4 polish (documented honest shortcuts — see
`CHANGES.md`): embedding-based thread↔action matching (current:
word-overlap), calibrated external-LLM evidence weight (current 0.4 is a
placeholder), a real explore-mode decision ladder (current: documented
INVESTIGATE stub), `EXTERNAL_LLM` as its own source type (current: HUMAN
source + `source_llm` column), case-type-specific decision symbols, and
proposed-edge accept/reject as real `EVENT_TYPES` entries (current: INFO
logs only).

See `CHANGES.md` for the full revamp change log and `AGENTS.md` for the
guardrails that AI implementing agents must follow.