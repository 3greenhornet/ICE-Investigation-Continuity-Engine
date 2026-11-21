-- =====================================================================
-- 016_contradiction_uniqueness.sql
--
-- Prevents duplicate contradiction rows when detect_contradiction is
-- called repeatedly on the same claim pair.
--
-- Two claims can contradict in exactly one way. Re-running the detection
-- pipeline for the same (case_id, claim_a, claim_b) triple should return
-- the existing contradiction, not insert a new one.
--
-- Preconditions: 012 (contradictions table).
-- Idempotent: uses IF NOT EXISTS on the constraint.
--
-- *** DEDUP PRECONDITION ***: if the database already has duplicate
-- contradictions from running the pipeline multiple times, the
-- uq_contradictions_claim_pair ADD CONSTRAINT below will FAIL with a
-- unique-violation. Run scripts/dedupe_contradictions.sql FIRST on live
-- databases. On fresh databases, no dedup is needed.
-- =====================================================================

-- Use LEAST/GREATEST to normalise direction: (A,B) and (B,A) map to the same key.
ALTER TABLE contradictions
   ADD COLUMN IF NOT EXISTS claim_pair_key TEXT
   GENERATED ALWAYS AS (
      LEAST(claim_a, claim_b) || '/' || GREATEST(claim_a, claim_b)
   ) STORED;

-- Unique on (case_id, claim_pair_key) — at most one contradiction per claim pair per case
ALTER TABLE contradictions
   DROP CONSTRAINT IF EXISTS uq_contradictions_claim_pair;

ALTER TABLE contradictions
  ADD CONSTRAINT uq_contradictions_claim_pair
  UNIQUE (case_id, claim_pair_key);

-- Also unique per (case_id, claim_a, claim_b) explicitly — belt and suspenders
ALTER TABLE contradictions
   DROP CONSTRAINT IF EXISTS uq_contradictions_direct;

ALTER TABLE contradictions
  ADD CONSTRAINT uq_contradictions_direct
  UNIQUE (case_id, claim_a, claim_b);

CREATE INDEX IF NOT EXISTS idx_contradictions_pair
    ON contradictions(case_id, claim_pair_key);