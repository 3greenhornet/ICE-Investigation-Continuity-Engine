# Stage 7 — Eval test suite: BLOCKED in this sandbox (skips clean)

**Status: blocked-user-side** — the 5 eval tests COLLECT cleanly and SKIP
cleanly here, but they cannot RUN: they need the live DB and a configured LLM
provider. Honest sandbox result from `05_unit_tests.txt`:

```
$ python3 -m pytest tests/integration/ tests/eval/ -q
86 skipped in 1.16s      # includes the 5 eval tests; 0 failed, 0 errored
```

## Why blocked

`tests/eval/test_benchmark_cases.py` (doc Part 3 §5.5 example 3, kept verbatim
+ one documented addition) runs `run_engine_on_case` on
`benchmark/cases/case_01_latency` — the full pipeline (LLM claim extraction
against a live provider + DB writes). The eval layer skips when (a) the
benchmark fixture dir is absent, (b) no DB pool, or (c) — fix-3's addition —
no provider key is set, because `extract_claims` fails loudly without one.

## What the user must run (doc Part 4 §8.1, verbatim commands)

With containers up + an LLM key in `.env`:

```bash
# The suite — expect >= 5 tests passed (ours: 5), exit 0, < 10 min
docker compose exec api pytest tests/eval/ -m eval -v --tb=short
echo "Exit code: $?"
```

If a benchmark case fails, check `benchmark/cases/case_01_latency/evidence/`
has all 5 evidence files (`01_incident_report.md`, `02_deployment_notes.md`,
`03_engineer_comment.txt`, `04_metrics_snapshot.csv`, `05_duplicate_report.md`)
and `docker compose logs api` for the pipeline error.

Save the run output as `verification/<date>/07_eval_tests.txt`.