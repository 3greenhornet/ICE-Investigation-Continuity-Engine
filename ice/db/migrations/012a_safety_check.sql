-- =====================================================================
-- 012a_safety_check.sql — Pre-flight check before ICE migration 012.
--
-- Migration 012 (012_case_model.sql) creates ICE tables with names that
-- collide with legacy Hari tables (hypotheses, contradictions,
-- hypothesis_versions). Applying 012 to a database that already has the
-- legacy tables populated will either fail (column mismatch) or, if
-- wrapped in DROP IF EXISTS, destroy data.
--
-- Oct 23's 012_case_model.sql unconditionally drops the legacy tables.
-- That is dangerous on a live database. This script is the safety valve:
-- it aborts if legacy data exists, forcing the operator to explicitly
-- decide whether to truncate or migrate.
--
-- Migration runner order: 011_edge_weights.sql -> 012a_safety_check.sql ->
-- 012_case_model.sql -> 013_evidence_graph.sql -> 014_case_events.sql
-- =====================================================================

DO $$
DECLARE
  legacy_hypotheses_count BIGINT := 0;
  legacy_contradictions_count BIGINT := 0;
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'hypotheses') THEN
     EXECUTE 'SELECT COUNT(*) FROM hypotheses' INTO legacy_hypotheses_count;
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'contradictions') THEN
     EXECUTE 'SELECT COUNT(*) FROM contradictions' INTO legacy_contradictions_count;
  END IF;

  IF legacy_hypotheses_count > 0 OR legacy_contradictions_count > 0 THEN
     RAISE EXCEPTION
       'Migration 012 refuses to run: legacy hypotheses table has % rows, legacy contradictions has % rows. '
       'Back up and TRUNCATE first if you intend to replace them.',
       legacy_hypotheses_count, legacy_contradictions_count;
  END IF;

    RAISE NOTICE 'Migration 012a safety check passed (0 legacy rows). Proceeding.';
END $$;