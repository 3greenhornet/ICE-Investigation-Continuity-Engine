-- =====================================================================
-- dedupe_contradictions.sql — run BEFORE applying migration 016 on live
-- databases that have already accumulated duplicate contradiction rows.
--
-- Doc Part 2 Section 9.1 warning: the uq_contradictions_claim_pair
-- constraint added by 016 fails with a unique-violation when duplicates
-- exist. This script removes them, keeping the earliest row per claim
-- pair (lexicographically smallest contradiction_id) in either order.
--
-- On fresh databases, no dedup is needed — do not run this script.
--
-- Usage: psql "$DATABASE_URL" -f scripts/dedupe_contradictions.sql
-- =====================================================================

DELETE FROM contradictions c1
USING contradictions c2
WHERE c1.case_id = c2.case_id
  AND c1.contradiction_id > c2.contradiction_id -- keep the earliest (lexicographic by id)
  AND (
     (c1.claim_a = c2.claim_a AND c1.claim_b = c2.claim_b)
     OR (c1.claim_a = c2.claim_b AND c1.claim_b = c2.claim_a)
  );