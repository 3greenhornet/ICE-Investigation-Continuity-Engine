-- =====================================================================
-- 015_external_analysis_and_case_type.sql
--
-- Adds three features:
-- 1. External LLM analysis ingestion (evidence.source_llm)
-- 2. Hypothesis origin tracking (hypotheses.origin, origin_detail)
-- 3. Case type branching (cases.case_type)
--
-- All three are additive: existing rows get sensible defaults, no data loss.
-- Preconditions: 012 (cases, evidence, hypotheses tables), 013 (edges), 014 (events).
-- Idempotent: uses IF NOT EXISTS throughout. Safe to re-run.
-- =====================================================================

-- Feature 1: External LLM analysis tracking
ALTER TABLE evidence
   ADD COLUMN IF NOT EXISTS source_llm TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_evidence_source_llm
  ON evidence(case_id, source_llm)
  WHERE source_llm IS NOT NULL;

COMMENT ON COLUMN evidence.source_llm IS
  'Set to the LLM name (e.g. ''claude-3.5-sonnet'') for MODEL_INFERENCE evidence '
  'that came from ingest_external_analysis(). NULL for all other evidence types '
  'and for ICE''s own internal inferences.';

-- Feature 2: Hypothesis origin tracking
ALTER TABLE hypotheses
   ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'user'
     CHECK (origin IN ('user', 'external_llm', 'ice_generated', 'source_material'));

ALTER TABLE hypotheses
  ADD COLUMN IF NOT EXISTS origin_detail TEXT;

COMMENT ON COLUMN hypotheses.origin IS
  'Where the hypothesis came from. ''user'' = proposed via API/CLI. '
  '''external_llm'' = extracted from an external LLM analysis. '
  '''ice_generated'' = auto-proposed by an ICE cycle. '
  '''source_material'' = extracted from a document/source.';
COMMENT ON COLUMN hypotheses.origin_detail IS
  'Free-form detail, e.g. ''claude-3.5-sonnet, 2026-10-22T14:30Z''.';

-- Feature 3: Case type
ALTER TABLE cases
   ADD COLUMN IF NOT EXISTS case_type TEXT NOT NULL DEFAULT 'debug'
     CHECK (case_type IN ('debug', 'research', 'explore'));

CREATE INDEX IF NOT EXISTS idx_cases_type_status
  ON cases(case_type, status);

COMMENT ON COLUMN cases.case_type IS
    '''debug'' = confidence-threshold-based resolution (existing behavior). '
    '''research'' = synthesis completeness (no contradictions + no high-priority gaps). '
    '''explore'' = manual resolution (user marks the case resolved).';