# Baseline — 2026-01-24 (project-internal verification date)

> Note on dates: this verification directory is named `2026-01-24` to match the
> project-internal timeline used by the ICE-fixes work (see the dated section
> headers in `CHANGES.md`). The sandbox wall clock reports
> `2026-10-24T05:55:42+00:00`; command outputs below paste it verbatim where a
> tool emitted a timestamp. No output in this directory was edited to hide that.

## Git

`hari/` is **not an independent git repository**. It sits inside the
sandbox workspace repo (`/home/z/my-project/.git`), whose history is a
sequence of auto-snapshot commits with opaque UUID subjects and no
remote — not a usable HARI provenance chain. No HARI-specific commits,
branches, or tags exist.

```
Workspace repo HEAD (unrelated auto-snapshots): 1a2d05a 2026-10-23 10:47:32 +0000 — 573a5ae3-d4ef-4e28-83b8-7f0807ace444
Remote: (none configured)
Hari files incidentally tracked by the workspace repo: 334
```

**Recommendation for the user** (doc Part 4 §1.1): initialize a dedicated
repository at the unpacked tree and commit the verification artifact
(doc §15.2):
```bash
cd hari/
git init && git add . && git commit -m 'Verification baseline — ICE fixes merged (Oct 22 base + fixes doc)'
git add verification/ && git commit -m 'Verification artifact — 2026-01-24'   # doc §15.2
git remote add origin <your-url> && git push -u origin main                   # needed for Stage 11 CI
```

## Environment

Verification environment: **sandbox (no Docker, no Postgres server, no LLM keys)**.

```
Date: 2026-10-24T05:55:42+00:00
Hostname: c-6adc3395-145cf406-852baa47e5db
OS: Linux c-6adc3395-145cf406-852baa47e5db 5.10.134-013.15.kangaroo.al8.x86_64 #1 SMP Thu Jul 30 12:54:59 UTC 2026 x86_64 GNU/Linux
Python: Python 3.12.15
Docker: not available in this sandbox (`docker: command not found`)
Docker Compose: not available in this sandbox
Postgres (psql client): not installed
Postgres server: not running / not installed in this sandbox
LLM API keys: none set in this sandbox (values: n/a — nothing to redact)
```

### Installed Python packages relevant to verification (python3 -c imports)

```
fastapi: 0.128.0
pydantic: 2.12.5
asyncpg: 0.32.0
httpx: 0.28.1
pytest: 9.0.2
pytest_asyncio: 1.3.0
pgvector: 0.5.1
```

### Packages required by the full stack but ABSENT in this sandbox (stubbed in harnesses)

```
litellm: NOT INSTALLED (absent)
streamlit: NOT INSTALLED (absent)
transformers: NOT INSTALLED (absent)
testcontainers: NOT INSTALLED (absent)
google.genai: NOT INSTALLED (absent)
dotenv: installed
```

### Env vars (keys only, no values) — sandbox process env

```
(none of GEMINI_/GROQ_/OPENROUTER_/MISTRAL_/SCRAPFLY_/FIRECRAWL_ are set in this sandbox)
```

Note: the sandbox exports a `DATABASE_URL` with a non-Postgres scheme
(`file:...`, set for unrelated tooling). Root `conftest.py` treats any
non-`postgresql://` DSN as unset so the integration/eval layers SKIP instead of
erroring (fix-3's documented DSN-scheme guard) — that is why Stage 6/7 record
"skipped-clean" rather than failures here.

## Migration files present (db/migrations/)

```
002_decision_trace.sql
003_development_ledger.sql
004_hybrid_retrieval.sql
005_deassistant.sql
006_pursuits.sql
007_homeostatic_reward.sql
008_reflection_edits.sql
009_architectural_inversion.sql
010_session_ownership.sql
011_edge_weights.sql
012_case_model.sql
012a_safety_check.sql
013_evidence_graph.sql
014_case_events.sql
015_external_analysis_and_case_type.sql
016_contradiction_uniqueness.sql
017_proposed_edges.sql
```

Count: 17 SQL migration files (002–011 legacy, 012/012a/013/014 case model, 015 external-analysis+case_type, 016 contradiction-uniqueness, 017 proposed-edges).

## .env.example keys (values redacted)

```
DATABASE_URL=<set>
POSTGRES_USER=<set>
POSTGRES_PASSWORD=<set>
POSTGRES_DB=<set>
GEMINI_API_KEY=<set>
GROQ_API_KEY=<set>
OPENROUTER_API_KEY=<set>
MISTRAL_API_KEY=<set>
LLM_FALLBACK_CHAIN=<set>
LITELLM_NETWORK_TIMEOUT=<set>
DOCLING_ENABLED=<set>
SCRAPER=<set>
SCRAPFLY_API_KEY=<set>
FIRECRAWL_API_KEY=<set>
ENABLE_CONTRADICTIONS=<set>
ENABLE_HYPOTHESIS_VERSIONING=<set>
ENABLE_FALSIFICATION_LOOP=<set>
ENABLE_NEXT_BEST_EVIDENCE=<set>
ENABLE_REPLAY=<set>
ENABLE_BACKGROUND_CASE_REVIEW=<set>
ENABLE_SOURCE_INDEPENDENCE=<set>
STERILE_MODE=<set>
ENABLE_DETERMINISTIC_CASCADES=<set>
ENABLE_GRACE=<set>
ENABLE_VOLITION_CAUSAL=<set>
ENABLE_REFLEXION=<set>
ENABLE_BROADCAST_FEEDBACK=<set>
ENABLE_PRM=<set>
ENABLE_LECTURER_PENALTY=<set>
ENABLE_SEMANTIC_REFRACTORY=<set>
ENABLE_AUTHORITY_CAUSAL=<set>
RETRIEVAL_PER_SOURCE_LIMIT=<set>
EXPERIMENT_SEED=<set>
CORS_ORIGINS=<set>
API_BASE_URL=<set>
```

Key count: 35 keys. Note: no `.env` is shipped in the deliverable; `.env.example` is the template (fix-2-c verified every engine-read variable is declared; NLI needs no env var).

## File inventory

### Total files (excluding __pycache__/, .git/, .pytest_cache/, logs/, verification/):
233

### Python files:
163

### Test files (tests/**/test_*.py):
20  (unit: 11, integration: 8, eval: 1)

### Migrations:
17