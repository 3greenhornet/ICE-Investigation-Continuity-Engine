# verification-report.md — HARI / ICE fixes merge (2026-01-24)

Per the fixes document's D.4 verification contract (extraction-pack lines
13232-13260). **Verification environment: sandbox — no Docker, no Postgres
server, no LLM keys, no browser.** Items that require those are marked NOT
RUN with the exact user-side command; no output below is fabricated. The
stage-by-stage artifact with full transcripts is in
`verification/2026-01-24/` (see its `SUMMARY.md`).

---

## 1. py_compile output

Contract command: `docker compose exec api python -m py_compile $(find
case_engine app cli -name '*.py')` — no container here, so the sandbox run
compiled **every** .py in the tree (superset of case_engine/app/cli):

```
Command: python3 -m py_compile over every .py under hari/
(excluding __pycache__/, .pytest_cache/, verification/)

Compiled: 163 files, 0 failed.
```

Source: `verification/2026-01-24/01_static_compile.txt`

## 2. Import smoke output

Contract command: `docker compose exec api python -c "import app.main,
case_engine, benchmark.runner"` — no container here. The sandbox equivalent
is the tree-wide stubbed import sweep (stub pattern: litellm / google.genai /
asyncpg / pgvector / streamlit / testcontainers / dotenv stand-ins; asyncpg
and pgvector ARE installed in this sandbox but the sweep stubs them anyway
for determinism — this proves the import graph, and the real-deps smoke runs
user-side with the contract command above):

```
Modules imported: 148 across 15 packages
Failures: 0
```

This is a **superset** of the doc Part 4 §2.2 41-module list (all 41 modules
are inside the sweep's package set; 148 individual modules + 15 packages +
explicit shim/rename and Part 2 entry-point assertions). Source:
`verification/2026-01-24/01_imports.txt`; runtime attribute checks
(EventLogger methods, NBA surface + debt bonus, action.py executors, claim
prefilter, P2 recompute, dispatchers, cycle signature, 015 schema columns,
NLI never-raises, executor mapping): 35/35 PASS in
`verification/2026-01-24/01_runtime_attrs.txt`.

## 3. Migration inventory (psql `\dt` NOT POSSIBLE — no Postgres in sandbox)

`docker exec ice-postgres psql -c "\dt"` cannot run here. File inventory
(instead) — all 17 migrations present:

```
002_decision_trace.sql            008_reflection_edits.sql         013_evidence_graph.sql
003_development_ledger.sql        009_architectural_inversion.sql  014_case_events.sql
004_hybrid_retrieval.sql          010_session_ownership.sql        012a_safety_check.sql
005_deassistant.sql               011_edge_weights.sql             015_external_analysis_and_case_type.sql
006_pursuits.sql                  012_case_model.sql               016_contradiction_uniqueness.sql
007_homeostatic_reward.sql                                         017_proposed_edges.sql
```

Fixes-document additions verified by content: `012a` (RAISE EXCEPTION safety
guard, P8), `015` (evidence.source_llm, hypotheses.origin/origin_detail,
cases.case_type + idx_cases_type_status), `016`
(contradictions.claim_pair_key + uq_contradictions_claim_pair +
uq_contradictions_direct + idx_contradictions_pair),
`017` (proposed_edges table with status CHECK + resolved_at). **Table
verification is user-side** via the Stage 2 commands in
`verification/2026-01-24/02_migrations_BLOCKED.md` (25-table presence query,
per-column checks, constraint/index checks, `\dx` vector extension; gate:
"ICE tables present: 14 / 14").

## 4. verify_part2.py — NOT RUN (no DB / no LLM keys)

User-side command:

```bash
docker compose exec api python scripts/verify_part2.py
```

Expected tail: all 8 steps print `[N/8]`, final line
`✅ Part 2 verification PASSED`, exit code 0 (plus the §4.1 LLM pre-check
showing a non-empty `Active models:` list). Full walkthrough:
`verification/2026-01-24/03_verify_part2_BLOCKED.md`.

## 5. verify_part3.py — NOT RUN (no DB / no LLM keys)

User-side command:

```bash
docker compose exec api python scripts/verify_part3.py
```

Expected tail: all 15 steps print, final line
`✅ Part 3 verification PASSED`, exit code 0, with
`ENABLE_NEXT_BEST_EVIDENCE=true` in the env dump. Full walkthrough:
`verification/2026-01-24/04_verify_part3_BLOCKED.md`.

## 6. Test collection count

```
$ python3 -m pytest tests/ --collect-only -q
...
239 tests collected in 1.25s
```

Breakdown: 153 unit (`-m unit`) + 81 integration (`-m integration`) + 5 eval
(`-m eval`). The unit layer **passes in this sandbox**: `153 passed in
1.26s`. The integration/eval layers **skip clean** here (86 skipped, 0
failed — no Postgres / no LLM key) and run user-side via
`docker compose exec api pytest tests/integration/ -m integration` /
`pytest tests/eval/ -m eval`. Source: `verification/2026-01-24/05_unit_tests.txt`.

## 7. P2 polarity test (doc P2.5, three-part)

Contract command runs inside the api container against live Postgres. The
sandbox equivalent runs the **same engine functions**
(create_case → _create_source → ingest_evidence → create_hypothesis /
create_claim → add_edge → recompute_hypothesis_confidence →
get_current_version) against an in-memory fake asyncpg pool reproducing the
012/013/015 column layouts — harness: `verification/2026-01-24/harnesses/
fix4_p2_polarity.py` (copied into the artifact so it is self-contained;
also at `.scratch/fix4_p2_polarity.py` in the dev environment;
fake-pool pattern from `.scratch/ice4a_live_http.py`). Output:

```
$ python3 /home/z/my-project/.scratch/fix4_p2_polarity.py

Test 1 (SUPPORTS direct): 0.5 -> 0.627 (expect > 0.5)
[PASS] P2.5 Test 1: evidence->hypothesis SUPPORTS raises confidence
Test 2 (CONTRADICTS direct): 0.5 -> 0.373 (expect < 0.5)
[PASS] P2.5 Test 2: evidence->hypothesis CONTRADICTS lowers confidence
Test 3 (claim CONTRADICTS): 0.5 -> 0.419 (expect < 0.5)
[PASS] P2.5 Test 3: claim->hypothesis CONTRADICTS inverts polarity (Correction 6 fixed)
[PASS] Test 1 version carries the supporting evidence id
[PASS] Test 3 version carries the inverted (contradicting) evidence id
P2 VERIFIED (all 3 polarity cases correct)

ALL P2 POLARITY CHECKS PASSED
```

(Test 3 failing with confidence going UP would mean the old Oct 22 loop is
still active — it is not: the claim CONTRADICTS branch inverts polarity.)

---

## Declarations

- **NLI classifier:** **implemented** — `case_engine/nli.py` (roberta-large-mnli
  behind the optional `[nli]` extra), wired into `extract_claims`
  (proposed_edges, status pending, never auto-committed), surfaced through
  the API (`/cases/{id}/proposed-edges` + accept/reject) and the UI review
  panel. **Model inference NOT verified here** (transformers/torch absent in
  this sandbox); the never-raises contract IS verified
  (`classify_claim_against_hypothesis` returns None without raising —
  `01_runtime_attrs.txt`). Inference check user-side after
  `pip install -e ".[nli]"`.
- **Explore mode:** **stub** per doc Part 2 §3.2 design note / §12 shortcut #6 —
  `_decide_explore` always returns INVESTIGATE; labeled as a stub in the
  source and in README/CHANGES/SUMMARY limitations.
- **Recovery scan:** **skipped: no Oct 23 directory** — the Oct 23 tree was
  never provided to this environment; there was nothing to inventory, diff,
  or recover. Dispositioned in CHANGES.md ("Merge note (ICE fixes doc Part 3
  §0.10) — 2026-01-24"); seed_demo.py is a §0.7 Case-1-equivalent
  (not recoverable → load_demo_case.py is the single canonical demo script).
  Full record: `verification/2026-01-24/12_recovery_scan.md`.
- **Benchmark:** **not run: no LLM/DB** — zero benchmark numbers exist in this
  deliverable, by the honesty mandate (doc Part 3 §9.1): every metric must be
  a command output. Smoke + full + baselines + ablation commands:
  `verification/2026-01-24/08_benchmark_BLOCKED.md`.

## Contract closing note

Items 4–7 of the D.4 contract: item 6 and 7 are green in-sandbox (239
collected; P2 polarity PASS); items 4 and 5 are NOT RUN and explicitly not
claimed — per the contract's "If any of 4-7 fails, do not claim completion,"
this report does not claim live-DB completion; it claims exactly the sandbox
scope above and hands the user the exact commands for the rest.