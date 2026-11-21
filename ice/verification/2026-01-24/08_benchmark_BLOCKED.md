# Stage 8 — Benchmark (smoke, full, baselines, ablation): BLOCKED in this sandbox

**Status: blocked-user-side** (benchmark requires the live DB + configured LLM
providers — `run_engine_on_case` performs real claim extraction; this sandbox
has neither). **No benchmark numbers exist in this verification artifact, by
design.** The doc's honesty mandate (Part 3 §9.1 / Part 4 §9.5): every metric
is either a command output or it does not get written. Nothing here is
fabricated; see `SUMMARY.md` — "Resume numbers: NOT AVAILABLE".

## Why blocked

The benchmark pipeline (`benchmark/runner.py`) ingests each case's evidence
into the DB and runs the LLM chain (extract_claims → hypotheses → decision).
Without Postgres + keys, every case would produce an error stub, and error
stubs are not metrics.

What WAS verified here: `benchmark/runner.py`, `benchmark/metrics.py`,
`benchmark/baselines.py`, `benchmark/ablation.py` compile and import under
the stubbed sweep (`01_imports.txt`); `report()`'s 8 keys and the
B4/B5/B6 flag plumbing (`set_env_flags`/`restore_env_flags`) are asserted by
the doc Part 1 §8.5 greps in `13_acceptance_checklist.md`.

## What the user must run (doc Part 4 §9.1–§9.5, verbatim commands)

```bash
# §9.1 Smoke test (1 case) — expect JSON with all 8 report keys, non-null values
docker compose exec api python -c "
import asyncio, json
from benchmark.runner import run_engine_on_case
from benchmark.metrics import report

async def main():
    result = await run_engine_on_case('benchmark/cases/case_01_latency')
    rep = report([result])
    print(json.dumps(rep, indent=2, default=str))

asyncio.run(main())" | tee verification/<date>/08_benchmark_smoke.json

# §9.2 Full benchmark (5 cases) — expect 5 case entries + 8 metrics + _meta
docker compose exec api python -c "
import asyncio, json, time
from benchmark.runner import run_all_cases
from benchmark.metrics import report

async def main():
    start = time.time()
    results = await run_all_cases('benchmark/cases')
    rep = report(results)
    rep['_meta'] = {
        'total_seconds': round(time.time() - start, 2),
        'case_count': len(results),
        'error_count': sum(1 for r in results if 'error' in r),
    }
    print(json.dumps(rep, indent=2, default=str))
asyncio.run(main())" | tee verification/<date>/08_benchmark_full.json

# §9.3 Baselines (B1-B6 on all cases) — expect 6 baselines x 5 cases = 30 entries
docker compose exec api python -c "
import asyncio, json, time
from benchmark.baselines import BASELINES

async def main():
    case_names = ['case_01_latency', 'case_02_memory_leak', 'case_03_auth_failure',
                  'case_04_data_inconsistency', 'case_05_deploy_loop']
    question = 'What is the root cause?'
    results = {}
    for name, fn in BASELINES.items():
        results[name] = {}
        for case_name in case_names:
            t0 = time.time()
            try:
                out = await fn(case_name, question)
                results[name][case_name] = {
                    'output': out[:500] if isinstance(out, str) else str(out)[:500],
                    'duration_seconds': round(time.time() - t0, 2),
                    'error': None,
                }
            except Exception as exc:
                results[name][case_name] = {
                    'output': None,
                    'duration_seconds': round(time.time() - t0, 2),
                    'error': str(exc),
                }
    print(json.dumps(results, indent=2))
asyncio.run(main())" | tee verification/<date>/08_benchmark_baselines.json

# §9.4 Ablation — expect 20 rows (4 configs x 5 cases) or error stubs
docker compose exec api python -c "
import asyncio, json
from benchmark.ablation import run_ablation_matrix

async def main():
    results = []
    for case_name in ['case_01_latency', 'case_02_memory_leak', 'case_03_auth_failure',
                      'case_04_data_inconsistency', 'case_05_deploy_loop']:
        case_dir = f'benchmark/cases/{case_name}'
        try:
            matrix = await run_ablation_matrix(case_dir)
            for row in matrix:
                row['case_name'] = case_name
            results.extend(matrix)
        except Exception as exc:
            results.append({'case_name': case_name, 'error': str(exc)})
    print(json.dumps(results, indent=2, default=str))
asyncio.run(main())" | tee verification/<date>/08_benchmark_ablation.json

# §9.5 Compute the resume numbers — print, record, and only then write any resume bullet
python3 -c "
import json
with open('verification/<date>/08_benchmark_full.json') as f:
    content = f.read()
start = content.find('{')
data = json.loads(content[start:])
print('=== RESUME NUMBERS (from actual measurements) ===')
print(f'Cases: {data[\"cases\"]}')
print(f'Hypothesis ranking accuracy: {data[\"hypothesis_ranking_accuracy\"]:.2%}')
print(f'Contradiction precision: {data[\"contradiction_precision\"]:.2%}')
print(f'Evidence trace coverage: {data[\"evidence_trace_coverage\"]:.2%}')
print(f'Mean latency: {data[\"mean_latency_seconds\"]:.1f}s')
print(f'Decision accuracy: {data[\"decision_accuracy\"]:.2%}')
print(f'Investigation efficiency: {data[\"investigation_efficiency\"]:.3f}')
print(f'Evidence utilization: {data[\"evidence_utilization\"]:.2%}')
print('_meta:', data.get('_meta', {}))"
```

Gate (doc §9.6): all four JSON files exist and are non-empty; `report()`
produces all 8 keys; no metric is `NaN`/`Infinity`; resume numbers printed and
recorded. Critical ablation check: if `full_engine` == `no_contradictions`
for every case, `ENABLE_CONTRADICTIONS` is a no-op (known B4 advisory flag —
document it in the summary rather than hiding it).