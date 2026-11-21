# ICE test suite (20 files, 239 tests)

The complete pytest suite for the Investigation Continuity Engine, authored
per the implementation spec Part 3 §5 (test-suite spec). Three layers, three
markers, one runner.

## Layout

| Path                                | Marker         | Requires                                   | Files | Tests |
|-------------------------------------|----------------|--------------------------------------------|-------|-------|
| `tests/unit/`                       | `unit`         | Nothing (no DB, no LLM; optional deps stubbed) | 11    | 153   |
| `tests/integration/`                | `integration`  | A Postgres 16 + pgvector database          | 8     | 81    |
| `tests/eval/`                       | `eval`         | DB **and** at least one LLM provider key (slow) | 1 | 5 |

```
tests/
├── conftest.py          # optional-dep stubs (litellm, google.genai) so
│                        # collection never breaks in slim environments
├── unit/                # no DB, no LLM — pure functions + monkeypatched pools
│   ├── test_llm.py                      # _extract_json_safely, _maybe_seed (crc32)
│   ├── test_case.py                     # create_case validation, UTC promotion, per-type confidence defaults
│   ├── test_evidence_classify.py        # classify_evidence_type pattern table
│   ├── test_claim_normalize.py          # _tokenize_statement / _statement_overlap (Jaccard)
│   ├── test_hypothesis_normalize.py     # _normalize_statement, _evidence_weight (source_llm 0.4 rule)
│   ├── test_contradiction_scope.py      # check_scope_compatibility (doc §5.3 example 1, scope class)
│   ├── test_contradiction_temporal.py   # check_temporal_compatibility (doc §5.3 example 1, temporal class)
│   ├── test_information_gap_priority.py # compute_gap_priority normalized entropy
│   ├── test_investigation_thread_debt.py# compute_debt formula, age floor/cap
│   ├── test_next_best_action_formula.py # EIG / impact / falsification / debt bonus / factor shape
│   └── test_decision_rules.py           # debug ladder + research/explore branches (Part 2 §3)
├── integration/         # real Postgres; LLM calls monkeypatched where the doc allows
│   ├── test_case_crud.py                # create/get/status/list/summary
│   ├── test_evidence_ingestion.py       # ingestion boundary, quality floors, dup clusters,
│   │                                    # supersede chains, ingest_external_analysis + dedup
│   ├── test_claim_extraction.py         # extract_claims pipeline (LLM monkeypatched)
│   ├── test_hypothesis_versioning.py    # doc §5.4 example 2 verbatim (13 tests)
│   ├── test_contradiction_detection.py  # detect_contradiction incl. Part 2 §9 idempotency
│   ├── test_action_lifecycle.py         # proposed/approved/executed + edit + SEARCH key guard
│   ├── test_investigation_cycle.py      # run_investigation_cycle: events, lifecycle, auto-execute
│   └── test_replay.py                   # replay / counterfactual / attribution
└── eval/
    └── test_benchmark_cases.py          # doc §5.5 example 3 verbatim (5 pipeline smoke tests)
```

## Run commands

```bash
# from the repo root (hari/), inside the container or a configured venv:

pytest tests/unit/ -m unit -q             # ~1.3s, no DB, no keys          (153 pass)
pytest tests/integration/ -m integration  # needs DB (see below)           (81 tests)
pytest tests/eval/ -m eval                # needs DB + LLM keys, ~5+ min   (5 tests)
pytest tests/ -q                          # everything: 153 pass + 86 skip without DB/keys

# single file / single test
pytest tests/unit/test_contradiction_scope.py -v
pytest tests/unit/test_decision_rules.py::TestDecideResearchMode -v

# with coverage (needs pytest-cov installed)
pytest tests/ --cov=case_engine --cov=app --cov-report=term-missing
```

## How the DB is provided

`conftest.py` (repo root) supplies:

- `postgres_container` — session-scoped testcontainers Postgres 16 +
  pgvector (`pgvector/pgvector:pg16`); **skips** (not errors) when
  testcontainers is not installed.
- `db_pool` — function-scoped asyncpg pool installed as
  `db.connection._pool`. When `DATABASE_URL` is set to a
  `postgresql://`/`postgres://` DSN (the CI integration job's service
  container) that database is used; a `DATABASE_URL` with any other scheme
  (e.g. a `file:` DSN from unrelated tooling) is ignored and treated as
  unset. Migrations **012a → 012 → 013 → 014 → 015 → 016 → 017** are
  applied once per session on a fresh database (012a is the legacy-data
  safety check that must precede 012's table drops; 015 adds
  source_llm/origin/case_type, 016 adds the contradiction claim_pair_key
  uniqueness, 017 adds proposed_edges).
- `case_id` — a freshly created case via `case_engine.case.create_case`.

Integration/eval tests therefore **skip cleanly** in any environment
without a database — `pytest tests/` stays green everywhere.

## Sandbox / CI ceilings (what can run where)

| Environment                          | unit | integration                    | eval                          |
|--------------------------------------|------|--------------------------------|-------------------------------|
| Slim sandbox (no DB, no keys)        | ✅   | skipped                        | skipped                       |
| `DATABASE_URL` → pgvector Postgres   | ✅   | ✅ (LLM steps monkeypatched)   | skipped (no LLM key guard)    |
| DB + one provider key (CI eval job)  | ✅   | ✅                              | ✅ (~30–60s per case)          |

Notes:

- **LLM calls are never made by unit/integration tests.** Where a code path
  would call the LLM (claim extraction, contradiction implication), the test
  monkeypatches `_call_llm_json` / `llm_implication_check` and exercises
  everything downstream against the real database. Only the eval layer
  spends real tokens.
- **Embeddings**: duplicate-cluster tests monkeypatch
  `case_engine.evidence.embed` with a deterministic 768-dim fake (no Gemini
  key needed); all other paths tolerate the embedder being unavailable.
- `tests/conftest.py` installs `sys.modules` stubs for the optional runtime
  deps `litellm` and `google.genai` (via `setdefault`, so a real install
  always wins) — without them, merely *collecting* tests that import
  `case_engine.contradiction` would fail in slim environments. The stubs
  raise if ever actually called.

## Writing conventions (kept from the spec's three complete examples)

- Every file carries a module docstring: what it covers, what it needs, and
  every deviation from the doc outline with the engine behavior that
  justified it.
- Markers: `@pytest.mark.unit` / `@pytest.mark.integration` / `@pytest.mark.eval`
  plus `@pytest.mark.asyncio` on async tests (`asyncio_mode = auto` in
  `pytest.ini` makes the latter redundant but keeps the doc's style).
- Unit tests for DB-backed modules monkeypatch the module-level
  `get_pool` (or the specific loaders) with in-memory fakes; assertions
  cover validation order, SQL parameter shapes, and event emission paths.
- A fourth marker, `benchmark`, is registered in `pytest.ini` for the
  benchmark harness under `benchmark/`.