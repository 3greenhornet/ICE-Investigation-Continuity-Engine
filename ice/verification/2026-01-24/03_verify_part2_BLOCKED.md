# Stage 3 — verify_part2.py live run: BLOCKED in this sandbox

**Status: blocked-user-side** (requires the running api container with a live
Postgres + at least one configured LLM key; this sandbox has neither Docker nor
LLM keys).

## Why blocked

`scripts/verify_part2.py` drives the case → evidence → claim → hypothesis →
version → supersede chain end-to-end against the live database with live LLM
calls. The doc's Stage 3 pre-flight (LLM connectivity via
`ACTIVE_FALLBACK_MODELS`) would fail here with an empty provider list — in this
sandbox no `GEMINI/GROQ/OPENROUTER/MISTRAL` keys are set and `litellm` is not
installed.

What WAS verified here (static + unit equivalents):

- The script itself compiles and imports under the stubbed sweep
  (`01_imports.txt` — scripts.* modules are part of the 148).
- Each of the 8 steps' engine functions is covered by the Stage 5 unit suite:
  case create validation/defaults (`tests/unit/test_case.py`, 21 tests),
  evidence ingest + DEFAULT_QUALITY_SCORES floors + override/clamp
  (`test_evidence_classify.py`, 16), claim extraction/normalization
  (`test_claim_normalize.py`, 16), hypothesis create/normalize
  (`test_hypothesis_normalize.py`, 12), versioning semantics
  (`tests/integration/test_hypothesis_versioning.py`, 13 — DB-layer),
  supersede/lifecycle (integration `test_evidence_ingestion.py`).
- The 8-step script and its expectations were reviewed as part of the Oct 22
  base (unchanged by the merge; CHANGES.md "Kept Oct 22" section).

## What the user must run (doc Part 4 §4, verbatim commands)

```bash
# §4.1 LLM connectivity pre-check — expect: Active models: ['groq/...', ...]
#                                                Test response: {'status': 'ok'}
docker compose exec api python -c "
import asyncio
from case_engine.llm import ACTIVE_FALLBACK_MODELS, _call_llm_json
print(f'Active models: {ACTIVE_FALLBACK_MODELS}')
if not ACTIVE_FALLBACK_MODELS:
    print('FAIL: No LLM providers configured'); exit(1)
async def t():
    r = await _call_llm_json('Return exactly: {\"status\": \"ok\"}')
    print(f'Test response: {r}')
    if r is None or r.get('status') != 'ok':
        print('FAIL: LLM call did not return expected JSON'); exit(1)
asyncio.run(t())"

# §4.2 the script itself — expect all 8 steps print [N/8], final line
#        "Part 2 verification PASSED", exit code 0
docker compose exec api python scripts/verify_part2.py; echo "Exit code: $?"

# §4.3 record the case id for the Stage 9 CLI walkthrough
CASE_ID_2=$(grep -oP 'Case created: \Kcase_[a-f0-9]+' verification/<date>/03_verify_part2.txt | head -1)
```

Save the full output as `verification/<date>/03_verify_part2.txt`. Step-by-step
failure recovery is doc Part 3 §3.1–§3.8 (`[1/8]` case creation … `[8/8]`
status + summary).