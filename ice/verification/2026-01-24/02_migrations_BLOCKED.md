# Stage 2 — Migration verification: BLOCKED in this sandbox

**Status: blocked-user-side** (no Docker daemon, no Postgres server, no psql
client in this verification sandbox).

## Why blocked

Stage 2 (doc Part 4 §3) verifies the schema of a LIVE database: container
status, `pg_isready`, `information_schema` queries for table/column/
constraint/index presence, and the `vector` extension. This sandbox has no
Docker (`docker: command not found`), no Postgres server, and no `psql`
client — there is no database to inspect.

What WAS verified here, statically:

- **Migration file inventory** — all 17 SQL files present (see
  `00_baseline.md`), including the four the fixes document adds:
  `012a_safety_check.sql` (P8), `015_external_analysis_and_case_type.sql`,
  `016_contradiction_uniqueness.sql`, `017_proposed_edges.sql`.
- **Migration content** — `012a` contains the `RAISE EXCEPTION` safety guard
  (doc Part 1 §8.4); `015` adds `evidence.source_llm`, `hypotheses.origin` +
  `origin_detail`, `cases.case_type` (+ index `idx_cases_type_status`);
  `016` adds `contradictions.claim_pair_key` + `uq_contradictions_claim_pair`
  + `uq_contradictions_direct` + `idx_contradictions_pair`; `017` creates
  `proposed_edges` with `status`/`resolved_at` columns.
- **Runner support** — `scripts/apply_ice_migrations.py` covers the
  ALTER-only migrations (012a/015/016/017) via its `ice_migration_history`
  idempotency table (fix-1 work; the doc's §2 pre-flight "skips files
  without CREATE TABLE" bug is fixed).
- The **engine code paths** those columns feed (schemas, ingest, decide
  branches) are runtime-verified in `01_runtime_attrs.txt` and Stage 5.

## What the user must run (doc Part 4 §3, verbatim commands)

Bring the stack up and apply migrations first (from the unpacked `hari/`):

```bash
# Env: cp .env.example .env  — then fill DATABASE_URL + at least one LLM key
docker compose build
docker compose up -d
sleep 15
docker exec ice-postgres pg_isready -U postgres        # expect: accepting connections
# Apply the ICE migrations (002-011 are applied by rebuild_db.sh / initdb;
# 012a->017 go through the idempotent runner):
docker compose exec api python scripts/apply_ice_migrations.py
```

Then the Stage 2 checks (expectations in brackets):

```bash
docker compose ps                                          # [ice-postgres + api + ui running]

# §3.3 table presence — expect 25 rows (14 ICE tables + legacy set, proposed_edges included)
docker exec ice-postgres psql -U postgres -d ice_cognitive -t -c "
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND table_name IN (
  'cases','sources','evidence','claims','hypotheses','hypothesis_versions',
  'contradictions','information_gaps','investigation_threads','actions',
  'decisions','evidence_graph_edges','case_events','proposed_edges',
  'memories','pursuits','development_events','system_interests','patterns',
  'curiosity_nodes','curiosity_edges','archived_memories','narrative_threads',
  'decision_traces','trace_workspace_items') ORDER BY table_name;"

# §3.4 column presence — each query must return its column name(s)
docker exec ice-postgres psql -U postgres -d ice_cognitive -t -c "SELECT column_name FROM information_schema.columns WHERE table_name='evidence' AND column_name='source_llm';"        # [source_llm]
docker exec ice-postgres psql -U postgres -d ice_cognitive -t -c "SELECT column_name FROM information_schema.columns WHERE table_name='evidence' AND column_name='quality_score';"    # [quality_score]
docker exec ice-postgres psql -U postgres -d ice_cognitive -t -c "SELECT column_name FROM information_schema.columns WHERE table_name='evidence' AND column_name='metadata';"         # [metadata]
docker exec ice-postgres psql -U postgres -d ice_cognitive -t -c "SELECT column_name FROM information_schema.columns WHERE table_name='hypotheses' AND column_name IN ('origin','origin_detail') ORDER BY column_name;"  # [origin, origin_detail]
docker exec ice-postgres psql -U postgres -d ice_cognitive -t -c "SELECT column_name FROM information_schema.columns WHERE table_name='cases' AND column_name='case_type';"          # [case_type]
docker exec ice-postgres psql -U postgres -d ice_cognitive -t -c "SELECT column_name FROM information_schema.columns WHERE table_name='contradictions' AND column_name='claim_pair_key';"  # [claim_pair_key]

# §3.5 constraints — expect uq_contradictions_claim_pair + uq_contradictions_direct
docker exec ice-postgres psql -U postgres -d ice_cognitive -c "
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid='contradictions'::regclass AND conname LIKE '%claim_pair%';"

# §3.6 indexes — expect at least idx_evidence_case, idx_evidence_embedding,
# idx_cases_status, idx_cases_type_status, idx_events_case, idx_events_type,
# idx_contradictions_case, idx_contradictions_pair
docker exec ice-postgres psql -U postgres -d ice_cognitive -c "
SELECT tablename, indexname FROM pg_indexes
WHERE schemaname='public' AND tablename IN ('evidence','cases','case_events','contradictions')
ORDER BY tablename, indexname;"

# §3.7 vector extension — expect the vector extension listed
docker exec ice-postgres psql -U postgres -d ice_cognitive -c "\dx"

# §3.8 gate — expect "ICE tables present: 14 / 14"
TABLES=$(docker exec ice-postgres psql -U postgres -d ice_cognitive -t -c "
SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN (
  'cases','sources','evidence','claims','hypotheses','hypothesis_versions',
  'contradictions','information_gaps','investigation_threads','actions',
  'decisions','evidence_graph_edges','case_events','proposed_edges');" | tr -d ' ')
echo "ICE tables present: $TABLES / 14"
```

On success, save the output as `verification/<date>/02_migrations.txt`
(doc §0.1 layout) — the doc's §15.1 summary then flips Stage 2 to green.