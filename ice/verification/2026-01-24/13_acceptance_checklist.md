# Aggregate acceptance — 2026-01-24 (sandbox scope)

Two-column status vocabulary (honest, per the task contract):

- **[green-sandbox]** — verified by execution in this sandbox; evidence pointer given.
- **[green-static]** — verified by code inspection / grep / AST harness (no execution of the DB/LLM path needed); evidence pointer given.
- **[blocked-user-side]** — requires live Postgres / LLM keys / Docker / browser / git remote; the exact command is in the referenced stage file.
- **[waived]** — documented reason; nothing to run.

Doc references: Part 1 §8.1–§8.8, Part 2 §13.1–§13.7, Part 3 §11.1–§11.8, Part 4 §14.1.

---

## Part 1 — Merge acceptance

### §8.1 Merge pre-flight

| Item | Status | Evidence |
|---|---|---|
| Base created (Oct 22 tree as merge base) | **[green-sandbox]** | the tree at `/home/z/my-project/hari/` IS the Oct 22 extraction (85 files, task 0-2) + patches; CHANGES.md "Merge from Oct 23" section |
| All 8 patches (P1–P8) applied | **[green-static]** | fix-1 harness 7/7 (`.scratch/fix1_verify.py`, re-run in this pass — see final sweep in SUMMARY.md); per-patch detail in CHANGES.md |
| All 5 shared-bug fixes (S1–S5) applied | **[green-static]** | S1/S2 applied (greps below); S3/S4/S5 verified NOT shared by the Oct 22 base (fix-1 worklog; CHANGES.md merge note) |
| `py_compile` every .py — zero errors | **[green-sandbox]** | `01_static_compile.txt`: **Compiled: 163 files, 0 failed.** |
| `grep -rn "max_actions" case_engine/ app/` — zero matches (S1) | **[green-sandbox]** | pasted below: **0 matches** (also 0 across cli/ui/benchmark/scripts/engine per fix-2-b re-check) |
| `grep -n "DEFAULT_QUALITY_SCORES" case_engine/evidence.py` — ≥2 (P1) | **[green-sandbox]** | pasted below: **3 matches** |
| `grep -n "direct_ev_rows" case_engine/hypothesis.py` — ≥2 (P2) | **[green-sandbox]** | pasted below: **2 matches** |
| EventLogger 4 case-mode methods (R1) | **[green-sandbox]** | pasted below: grep finds set_turn/set_case_id/log_hari_expression/log_case_event (4 defs + log_background_tick); runtime hasattr check in `01_runtime_attrs.txt` |
| `STERILE_MODE` in engine/config.py = `_bool("STERILE_MODE", "true")` (R18) | **[green-sandbox]** | pasted below: line 54 `STERILE_MODE = _bool("STERILE_MODE", "true")` |
| `db/migrations/012a_safety_check.sql` exists (P8) | **[green-sandbox]** | pasted below: file listed |
| 7 doc files exist (R11) | **[green-sandbox]** | pasted below: **7/7** |

### §8.2 Runtime verification (docker-path)

| Item | Status | Evidence |
|---|---|---|
| `docker compose up -d --build` succeeds | **[blocked-user-side]** | `02_migrations_BLOCKED.md` (build + up commands) |
| `docker compose ps` — 3 healthy containers | **[blocked-user-side]** | same |
| `/api/v1/health/ready` → ok/ok/ok | **[blocked-user-side]** | same (needs DB+LLM). Sandbox near-equivalent: fix2b/ice4a harnesses drive the real ASGI stack against a fake pool — `/health` 200, `/health/ready` 200 degraded-db-ok (86/86, 43/43) |
| Migrations 002–011 applied | **[blocked-user-side]** | `02_migrations_BLOCKED.md` |
| `psql -c "SELECT COUNT(*) FROM cases;"` etc. (012/013/014 ran) | **[blocked-user-side]** | `02_migrations_BLOCKED.md` (25-table + column checks) |
| verify_part2.py / verify_part3.py PASSED | **[blocked-user-side]** | `03_verify_part2_BLOCKED.md`, `04_verify_part3_BLOCKED.md` |

### §8.3 Feature verification (patches in action — DB path)

| Item | Status | Evidence |
|---|---|---|
| MEASUREMENT without quality_score → 0.95 (P1) | **[green-sandbox]** (unit layer) + **[blocked-user-side]** (live API) | `tests/unit/test_evidence_classify.py` (16 pass) asserts the type-default floors; fix2b live-HTTP (fake pool) asserted MEASUREMENT 0.95 / OBSERVATION 0.65 end-to-end through POST /cases/{id}/evidence; user-side psql/API check in `02/03` files |
| MEASUREMENT with quality_score 0.9 → 0.9 (P1) | **[green-sandbox]** (unit + fake-pool) | integration `test_evidence_ingestion.py` (override 0.42 + clamp 7→1.0 cases, DB-layer, user-side); unit classify tests green here |
| evidence→hypothesis SUPPORTS edge raises confidence (P2) | **[green-sandbox]** (fake-pool harness) | `.scratch/fix4_p2_polarity.py` Test 1 PASS — see `verification-report.md` item 7 (doc P2.5 three-part test, sandbox form) |
| evidence→hypothesis CONTRADICTS lowers (P2) | **[green-sandbox]** (fake-pool harness) | same, Test 2 PASS |
| claim→hypothesis CONTRADICTS inverts polarity (Correction 6) | **[green-sandbox]** (fake-pool harness) | same, Test 3 PASS |
| evidence metadata round-trips (P5) | **[green-sandbox]** (fake-pool) | fix2b (c) checks + fix1 harness (d); live API user-side |
| summary next_action priority == investigate recommended priority (P6) | **[green-static]** + **[blocked-user-side]** | both call `select_next_best_action` (fix-1 R6/P6 patch; source grep in fix1_verify); numeric equality user-side |
| decision attribution 404 DECISION_NOT_FOUND (P7) | **[green-sandbox]** (fake-pool) | fix2b (c) regression spot-check + ice4a live-HTTP (`GET /decisions/missing/attribution -> 404 DECISION_NOT_FOUND`) |
| CLI `investigate run --falsify` accepted (P4) | **[green-sandbox]** | `09_cli_walkthrough_PARTIAL.md` — argparse round-trip PASS |

### §8.4 Migration safety (P8)

| Item | Status | Evidence |
|---|---|---|
| 012a passes on empty legacy tables / raises on populated | **[blocked-user-side]** | live-DB behavior; `02_migrations_BLOCKED.md`. Static half verified: file contains `RAISE EXCEPTION` guard |
| 012a file contains the RAISE EXCEPTION clause | **[green-sandbox]** | grep hit in `012a_safety_check.sql` (fix1 harness check 6/7) |

### §8.5 Regression avoidance (static greps — run in this pass)

| Item | Expected | Actual | Status |
|---|---|---|---|
| `grep -c "class WebSearchExecutor\|class FileInspectExecutor\|class HumanAskExecutor\|def default_executor_for" case_engine/action.py` | 4 | **4** | **[green-sandbox]** |
| `grep -c "def edit_action" case_engine/action.py` | 1 | **1** | **[green-sandbox]** |
| `grep -c "compute_priority_with_factors" case_engine/decision.py` | 1 | **3** (1 call site + 1 import + 1 docstring) | **[green-sandbox]** — the doc's intent (decision.py uses the factor-returning API, rule-11) holds; the literal count is 3 because the Oct 22 base imports the function and documents it. Call site: decision.py:215 |
| `grep -c "case_type" case_engine/schemas.py case_engine/decision.py` | multiple | **schemas 2, decision 18** | **[green-sandbox]** |
| `grep -c "ingest_external_analysis" case_engine/evidence.py` | 1 | **1** | **[green-sandbox]** |
| `grep -c "add_exception_handler" app/errors.py` | 15 | **1** (loop-based registration) | **[green-sandbox]** with documented deviation — the Oct 22 base registers handlers in a `for` loop over `ERROR_MAP` + one catch-all decorator instead of 15 literal calls. Live equivalence verified in this pass: `ERROR_MAP` has **14** entries; `register_exception_handlers(FastAPI())` registers **14 mapped + 1 catch-all = 15** handlers (18 total incl. FastAPI's 3 defaults). R13's actual acceptance — typed error bodies, no server-side tracebacks on 404s — is exercised green by the fix2b/ice4a fake-pool harnesses (404s return `ErrorResponse` bodies) |
| `grep -c "_maybe_seed" case_engine/llm.py` | ≥2 | **4** | **[green-sandbox]** |
| `grep -c "set_env_flags\|restore_env_flags" benchmark/baselines.py` | ≥4 | **8** | **[green-sandbox]** |
| `grep -c "detect_contradiction" benchmark/runner.py` | ≥1 | **5** | **[green-sandbox]** |
| `grep -c "intrinsic_relevance_base" engine/attention_instrumentation.py` | ≥1 | **2** | **[green-sandbox]** |

### §8.6 Behavioral verification (end-to-end)

| Item | Status | Evidence |
|---|---|---|
| `python scripts/load_demo_case.py` creates 3 cases, exit 0 | **[blocked-user-side]** | needs DB+LLM; script imports + 15 source markers verified by fix2c harness 97/97 (§6 rewrite three-case structure, case_type/defaults/external-analysis calls) |
| UI loads, demo case selectable | **[blocked-user-side]** | `10_ui_walkthrough_BLOCKED.md` |
| Run Investigation cycles without crashing | **[blocked-user-side]** | user-side; cycle logic unit/integration-covered (test_investigation_cycle.py, 9 tests) |
| api logs: no AttributeError / max_actions kwargs / set_turn | **[blocked-user-side]** | user-side; static equivalents all green (Stage 1 + §8.5 greps) |
| 404s produce no server-side tracebacks (R13) | **[green-sandbox]** (fake-pool) | ice4a live-HTTP + fix2b: typed 404 bodies across cases/evidence/gaps/actions/hypotheses/decisions |

### §8.7 Documentation check

| Item | Status | Evidence |
|---|---|---|
| README explains how to run | **[green-sandbox]** | README quickstart + Part 2 sections (fix2c keyword matrix, 18 markers) |
| AGENTS.md guardrails current | **[green-sandbox]** | fix2c (d): 10 markers incl. fixes-doc authority + NLI optional-dep contract |
| CHANGES.md notes the merge with a dated entry | **[green-sandbox]** | "## Merge from Oct 23 (ICE fixes doc Part 1) — 2026-01-24" + "## Merge note (ICE fixes doc Part 3 §0.10) — 2026-01-24" |
| docs/PROV.md intact | **[green-sandbox]** | fix2c (d): 7 markers incl. source_llm/origin/proposed-edge rows |

### §8.8 Test harness check

| Item | Status | Evidence |
|---|---|---|
| `pytest --collect-only` collects without errors | **[green-sandbox]** | `05_unit_tests.txt`: **239 tests collected**, 0 errors (153 unit + 81 integration + 5 eval) |
| pytest.ini has no orphaned --ignore flags | **[green-sandbox]** | pytest.ini pasted below (testpaths=tests, asyncio_mode=auto, 4 markers — no ignores) |
| conftest.py DSN replacement + async fixture | **[green-sandbox]** | root conftest.py: DSN-scheme guard (non-postgres → treated unset → testcontainers fallback → skip) + `case_id` async fixture; asyncio_mode=auto in pytest.ini |

---

## Part 2 — Scope-broadening acceptance (§13.1–§13.7)

### §13.1 Migration and schema

| Item | Status | Evidence |
|---|---|---|
| 015 applies cleanly on top of 014 | **[blocked-user-side]** (apply) / **[green-static]** (file + runner) | `02_migrations_BLOCKED.md`; apply_ice_migrations.py covers ALTER-only files via ice_migration_history (fix-1) |
| 016 applies cleanly on top of 015 | same | same |
| `\d evidence` shows source_llm | **[blocked-user-side]** | `02_migrations_BLOCKED.md` §3.4 commands; static: 015 contains the ADD COLUMN |
| `\d hypotheses` shows origin + origin_detail | **[blocked-user-side]** (same static note) | same |
| `\d cases` shows case_type | **[blocked-user-side]** (same static note) | same |
| `\d contradictions` shows claim_pair_key + both unique constraints | **[blocked-user-side]** (static: 016 has claim_pair_key, uq_contradictions_claim_pair, uq_contradictions_direct) | same |
| `\d proposed_edges` exists with all columns | **[blocked-user-side]** (static: 017 CREATE TABLE with status CHECK + resolved_at) | same |

### §13.2 Feature verification (2A–2E)

| Item | Status | Evidence |
|---|---|---|
| `ingest_external_analysis` end-to-end | **[green-sandbox]** (fake-pool + unit) / live **[blocked-user-side]** | fix2b (c): POST from-llm → 201 MODEL_INFERENCE + source_llm + prompt_hash; (llm,prompt) dedup same-id; 400 whitespace; 404 missing case; integration test_evidence_ingestion (4 tests) |
| `case_type` branches decide + generate_actions | **[green-sandbox]** | `01_runtime_attrs.txt` (dispatch markers) + `tests/unit/test_decision_rules.py` 14 tests incl. research rules 1–4 + explore always-INVESTIGATE |
| WebSearchExecutor wiring via default_executor_for | **[green-sandbox]** | `01_runtime_attrs.txt` executor mapping (SEARCH/INSPECT/ASK_HUMAN/RETRIEVE/COMPARE) |
| collect_case_endogenous_candidates on aged cases | **[green-sandbox]** (fake-pool) / live **[blocked-user-side]** | fix2b (c): GET suggestions → 200 with the aged thread in §5.2 shape + [] for thread-less case |
| load_demo_case.py produces three cases | **[blocked-user-side]** (run) / **[green-static]** (structure) | fix2c (c): 15 source markers; three loader functions present |

### §13.3 Fix verification (2F–2J)

| Item | Status | Evidence |
|---|---|---|
| Hypothesis lifecycle transitions fire | **[green-sandbox]** (fake-pool/unit) / live **[blocked-user-side]** | fix2b (c) `_to_result` lifecycle_changes; integration test_investigation_cycle: real PROPOSED→SUPPORTED via recompute sigmoid(1.9)≈0.87 |
| Investigation debt wiring affects priority | **[green-sandbox]** | `tests/unit/test_next_best_action_formula.py`: debt-bonus word-overlap cap/no-overlap/no-threads cases + factors shape has debt_bonus (21 tests) |
| Contradiction idempotency (re-runs don't duplicate) | **[green-sandbox]** (unit/integration code) / live DB **[blocked-user-side]** | pre-check + ON CONFLICT DO NOTHING + claim_pair_key (016); integration test twice-run single-row; + orchestrator's self-pair guard (detect_contradiction returns None when claim_a_id == claim_b_id — contradiction.py:305, addresses fix-3's observation (c)) |
| NLI classifier proposes edges | **[waived]**-in-sandbox / **[blocked-user-side]** with extras | module + wiring + UI verified (`01_runtime_attrs.txt`: nli imports, classify returns None without transformers, never raises); model inference requires `pip install -e ".[nli]"` (transformers absent here) — declared NOT verified in `verification-report.md` |
| Homeostatic dead code removed (§11.1) | **[green-sandbox]** | fix2c (b) grep invariants: compute_homeostatic_reward absent from engine/+case_engine/+app/, homeostatic_delta only behind .get() in diversity_check, _last_homeostatic_reward zero tree-wide |
| add_edge limitation documented (§11.2) | **[green-sandbox]** | evidence_graph docstring + README deferred list (fix2c keyword matrix) |
| origin + source_llm round-trip through API | **[green-sandbox]** (fake-pool) | fix2b (b)+(c): CaseResponse/Summary case_type; EvidenceResponse source_llm; HypothesisResponse origin/origin_detail; GET /evidence/{id} returns source_llm |

### §13.4 Regression checks

| Item | Status | Evidence |
|---|---|---|
| docker compose up -d --build succeeds | **[blocked-user-side]** | `02_migrations_BLOCKED.md` |
| verify_part2.py passes (8 steps) | **[blocked-user-side]** | `03_verify_part2_BLOCKED.md` |
| verify_part3.py passes (15 steps) | **[blocked-user-side]** | `04_verify_part3_BLOCKED.md` |
| Debug demo produces complete cycle | **[blocked-user-side]** | `09_cli_walkthrough_PARTIAL.md` + load_demo_case run command |
| Hypothesis board shows version history | **[blocked-user-side]** (UI) / **[green-static]** | panel present (fix2b (f)); `10_ui_walkthrough_BLOCKED.md` |
| Evidence chain shows provenance | same | same |
| Decision debugger shows top factors | same | same |
| Investigation trace shows events | same | same |

### §13.5 CLI and UI

| Item | Status | Evidence |
|---|---|---|
| `ice evidence add-llm ...` works | **[green-sandbox]** (parser+dispatch) / live **[blocked-user-side]** | `09_cli_walkthrough_PARTIAL.md` (d) output + user-side command |
| `ice case create --type research` works | **[green-sandbox]** (parser+dispatch) / live **[blocked-user-side]** | same |
| `ice investigate run --auto-execute` works | **[green-sandbox]** (parser+dispatch) / live **[blocked-user-side]** | same |
| `ice investigate run --falsify` works | **[green-sandbox]** (parser+dispatch) / live **[blocked-user-side]** | same |
| Dashboard case-type badge | **[green-static]** / render **[blocked-user-side]** | fix2b (f) marker checks; `10_ui_walkthrough_BLOCKED.md` |
| Dashboard "The system noticed" panel | **[green-static]** / render **[blocked-user-side]** | same |
| Dashboard "Thread Debt" panel | **[green-static]** / render **[blocked-user-side]** | same |
| Dashboard "Proposed Claim-Hypothesis Edges" panel | **[green-static]** / render **[blocked-user-side]** | same (on the Hypothesis Board screen, per implementation) |
| Hypothesis board origin badges | **[green-static]** / render **[blocked-user-side]** | same |

### §13.6 Documentation

| Item | Status | Evidence |
|---|---|---|
| README mentions the three case types | **[green-sandbox]** | fix2c (d) README keyword matrix (18 markers) |
| README documents ingest_external_analysis workflow | **[green-sandbox]** | same |
| AGENTS.md notes NLI optional dependency | **[green-sandbox]** | fix2c (d) AGENTS markers (10) |
| CHANGES.md entry for Part 2 work | **[green-sandbox]** | "## Part 2 scope-broadening — engine core" + "## Part 2 surface + operations" sections |
| docs/PROV.md mentions source_llm + origin | **[green-sandbox]** | fix2c (d) PROV markers (7) |

### §13.7 Honesty check

| Item | Status | Evidence |
|---|---|---|
| Every §12 shortcut has a code note | **[green-sandbox]** | fix2c (d) + fix2b (f) greps (0.4 placeholder note, explore stub label, no-op executors docstring, NLI honest note in nli.py, EVENT DISCIPLINE comments) |
| No fabricated numbers in docs/benchmark output | **[green-sandbox]** | no benchmark numbers exist anywhere in this artifact (see SUMMARY.md "Resume numbers: NOT AVAILABLE") |
| `_decide_explore` clearly labeled a stub | **[green-sandbox]** | decision.py:81 "(documented stub, doc Part 2 §12 shortcut #6)"; runtime-attrs check names it |
| RETRIEVE/COMPARE/RUN_TEST no-op executors documented | **[green-sandbox]** | action.py default_executor_for docstring HONEST NOTE (§4.6) |
| 0.4 external weight documented as placeholder | **[green-sandbox]** | hypothesis.py `_evidence_weight` + README + PROV + SUMMARY limitations |

---

## Part 3 + Part 4 — Operational acceptance

| Item | Status | Evidence |
|---|---|---|
| Stage 0: baseline captured | **[green-sandbox]** | `00_baseline.md` |
| Stage 1: compile + import clean | **[green-sandbox]** | `01_static_compile.txt` (163/0), `01_imports.txt` (148 modules, 0 failures), `01_runtime_attrs.txt` (35/35) |
| Stage 2: migrations applied, tables/columns/constraints present | **[blocked-user-side]** | `02_migrations_BLOCKED.md` (full command set + expectations) |
| Stage 3: verify_part2.py PASSED | **[blocked-user-side]** | `03_verify_part2_BLOCKED.md` |
| Stage 4: verify_part3.py PASSED | **[blocked-user-side]** | `04_verify_part3_BLOCKED.md` |
| Stage 5: unit tests pass (≥11 files, ≥70 tests) | **[green-sandbox]** | `05_unit_tests.txt`: **11 files, 153 passed in 1.26s** (targets: ≥11/≥70/<30s) — Part 3 §11.4 counts: 153 unit vs ≥70 |
| Stage 6: integration tests pass (≥8 files, ≥45 tests) | **[blocked-user-side]** (skips clean: 81 skipped, 0 failed) | `06_integration_tests_BLOCKED.md`; inventory 8 files/81 tests meets the file target statically; targets 81 vs ≥45 when run |
| Stage 7: eval tests pass (5 cases) | **[blocked-user-side]** (skips clean: 5 skipped) | `07_eval_tests_BLOCKED.md`; 5 tests vs ≥5 target |
| Stage 8: benchmark 4 JSONs with 8 metrics | **[blocked-user-side]** — **no numbers recorded** | `08_benchmark_BLOCKED.md` (honesty mandate §9.1: no fabricated metrics) |
| Stage 9: CLI walkthrough all commands work | **[partial]** — parser/dispatch verified here; live run **[blocked-user-side]** | `09_cli_walkthrough_PARTIAL.md` |
| Stage 10: UI walkthrough all screens render | **[blocked-user-side]** | `10_ui_walkthrough_BLOCKED.md` |
| Stage 11: CI three jobs green | **[blocked-user-side]** (workflow verified current statically) | `11_ci_BLOCKED.md`; test.yml: unit/integration/eval jobs run exactly `pytest -m unit|integration|eval`, integration job has pgvector/pg16 service + DATABASE_URL, eval job provides keys via secrets |
| Stage 12: no unaccounted Oct 23 files | **[waived]** with documented reason | `12_recovery_scan.md` — Oct 23 directory never provided; disposition in CHANGES.md merge note (§0.7 Case-1 for seed_demo.py) |

### Part 3 §11 extras

| Item | Status | Evidence |
|---|---|---|
| §11.1 env/.env keys, containers, health, UI loads | **[blocked-user-side]** | `02_migrations_BLOCKED.md` + `10_ui_walkthrough_BLOCKED.md`; `.env.example` complete (35 keys, fix2c verified) |
| §11.2 migrations idempotent re-run | **[blocked-user-side]** | apply_ice_migrations ice_migration_history (idempotency by filename) verified statically |
| §11.5 benchmark smoke/full/baselines/ablation + METRICS_SUMMARY | **[blocked-user-side]** | `08_benchmark_BLOCKED.md` |
| §11.6 debugging commands understood | **[waived]** | Part 3 §7 diagnostic tree — nothing to run here; failure-mode references embedded in each blocked file |
| §11.7 CI green on push | **[blocked-user-side]** | `11_ci_BLOCKED.md` |
| §11.8 resume numbers traceable | **[waived]** in sandbox | no benchmark run → no numbers → no bullets; mandate honored (SUMMARY.md) |

### Honesty checklist (doc §14.1)

| Item | Status | Evidence |
|---|---|---|
| No fabricated benchmark numbers | **[green-sandbox]** | SUMMARY.md states NOT AVAILABLE; `08_benchmark_BLOCKED.md` has commands only |
| Every resume bullet cites a Stage 8 output | **[waived]** (no bullets exist — no Stage 8 output exists) | SUMMARY.md |
| Known limitations documented | **[green-sandbox]** | SUMMARY.md limitations list (doc §15.1 verbatim) |
| Every Part 2 §12 shortcut has a code comment | **[green-sandbox]** | §13.7 table above |

## Final verdict (sandbox scope)

Static soundness + unit layer + API contract + harness evidence: **GREEN**.
Live-DB stages (2,3,4,6,7,8,10,11): **blocked-user-side — exact commands
recorded in this directory**. NOT "ready to ship" until the user-side commands
return green; the honest scope of this artifact is stated in SUMMARY.md.

---

## Appendix — grep battery outputs (pasted verbatim from this run)

```
$ grep -rn "max_actions" case_engine/ app/ | wc -l
0                                                    # S1: zero matches
$ grep -c "DEFAULT_QUALITY_SCORES" case_engine/evidence.py
3                                                    # P1: >=2
$ grep -c "direct_ev_rows" case_engine/hypothesis.py
2                                                    # P2: >=2
$ grep -n "def set_turn\|def set_case_id\|def log_hari_expression\|def log_case_event" engine/events.py | wc -l
4                                                    # R1: 4 method defs (+ log_background_tick at :183)
121:    def set_case_id(self, case_id: Optional[str]) -> None:
125:    def set_turn(self, turn_number: int) -> None:
162:    def log_hari_expression(
193:    async def log_case_event(
$ grep -n "STERILE_MODE" engine/config.py
52:# ICE spec Part 1 §9 (Configuration Reference): STERILE_MODE defaults to true
54:STERILE_MODE = _bool("STERILE_MODE", "true")       # R18
$ ls db/migrations/012a_safety_check.sql
db/migrations/012a_safety_check.sql                  # P8: exists
$ ls README.md AGENTS.md CHANGES.md docs/PROV.md pyproject.toml requirements.txt .env.example | wc -l
7                                                    # R11: 7 files
$ grep -c "class WebSearchExecutor\|class FileInspectExecutor\|class HumanAskExecutor\|def default_executor_for" case_engine/action.py
4                                                    # R5: 4
$ grep -c "def edit_action" case_engine/action.py
1                                                    # R4: 1
$ grep -c "compute_priority_with_factors" case_engine/decision.py
3                                                    # rule-11: call site :215 + import :38 + docstring :14
$ grep -c "case_type" case_engine/schemas.py case_engine/decision.py
case_engine/schemas.py:2
case_engine/decision.py:18                           # R7: multiple
$ grep -c "ingest_external_analysis" case_engine/evidence.py
1                                                    # R8: 1
$ grep -c "add_exception_handler" app/errors.py
1                                                    # R13: loop registration — 14 ERROR_MAP entries + 1
                                                     #   catch-all = 15 handlers registered (verified live
                                                     #   this pass); see table note above
$ grep -c "_maybe_seed" case_engine/llm.py
4                                                    # R16: >=2
$ grep -c "set_env_flags\|restore_env_flags" benchmark/baselines.py
8                                                    # R14: >=4
$ grep -c "detect_contradiction" benchmark/runner.py
5                                                    # R15: >=1
$ grep -c "intrinsic_relevance_base" engine/attention_instrumentation.py
2                                                    # R17: >=1
$ cat pytest.ini
[pytest]
testpaths = tests
asyncio_mode = auto
markers =
    unit: Fast, no DB, no LLM
    integration: Requires DB
    eval: Requires LLM (slow)
    benchmark: Full pipeline
filterwarnings =
    ignore::DeprecationWarning                       # §8.8: no orphaned --ignore flags
$ python3 -c "import json; print(len(json.load(open('docs/openapi.json'))['paths']))"
50                                                   # 44 (Oct 22) + 6 Part 2 routes
```