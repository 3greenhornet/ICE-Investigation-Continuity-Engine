# Stage 4 — verify_part3.py live run: BLOCKED in this sandbox

**Status: blocked-user-side** (requires the running api container with a live
Postgres + LLM keys; this sandbox has neither).

## Why blocked

`scripts/verify_part3.py` drives the contradiction detection → decision →
replay chain end-to-end (15 steps), including a live
`llm_implication_check` call in step 5. No Docker / Postgres / LLM keys exist
in this sandbox, so steps 1+ fail immediately at the first DB write.

What WAS verified here (static + unit equivalents):

- The script compiles and imports under the stubbed sweep (`01_imports.txt`).
- The chain's engine components are unit/integration-verified in Stage 5
  artifacts: contradiction scope/temporal pre-screens + idempotency +
  cross-case guard + self-pair guard (`tests/unit/test_contradiction_scope.py`
  11 tests, `test_contradiction_temporal.py` 9; integration
  `test_contradiction_detection.py` 9), decision ladder incl. case_type
  dispatch (`test_decision_rules.py`, 14), NBA factors incl. debt bonus
  (`test_next_best_action_formula.py`, 21), replay/event semantics
  (integration `test_replay.py`, 6).
- The doc's known Stage 4 gotcha (step 5 returning `None` on silent LLM
  failure) is mitigated in-code: `detect_contradiction` logs
  "Not a contradiction" outcomes and the cycle's idempotency pre-check
  (Part 2 §9.2) short-circuits repeat pairs.

## What the user must run (doc Part 4 §5, verbatim commands)

```bash
# §5.1 environment prerequisites — expect ENABLE_NEXT_BEST_EVIDENCE=true
#        (.env must set it for the verify script's NBA/ETC steps)
docker compose exec api python -c "
import os
print(f'ENABLE_FALSIFICATION_LOOP={os.getenv(\"ENABLE_FALSIFICATION_LOOP\",\"false\")}')
print(f'ENABLE_NEXT_BEST_EVIDENCE={os.getenv(\"ENABLE_NEXT_BEST_EVIDENCE\",\"false\")}')
print(f'ENABLE_CONTRADICTIONS={os.getenv(\"ENABLE_CONTRADICTIONS\",\"false\")}')"

# §5.1 the script itself — expect all 15 steps print, final line
#        "Part 3 verification PASSED", exit code 0
docker compose exec api python scripts/verify_part3.py; echo "Exit code: $?"
```

Save the full output as `verification/<date>/04_verify_part3.txt`.
Step-by-step failure recovery is doc Part 3 §4.1–§4.12 (`[1]-[4]` setup,
`[5]` contradiction detection, `[6]` gap priority, `[7]` thread reinforce,
`[8]` hypothesis graph, `[9]` ETC/independence, `[10]` action generation,
`[11]` NBA, `[12]` decision, `[13]` replay, `[14]` causal influence
(0.000 negligible is EXPECTED), `[15]` summary).