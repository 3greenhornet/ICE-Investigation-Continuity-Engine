-- =====================================================================
-- 017_proposed_edges.sql
--
-- Fix 2I support table: NLI-proposed claim->hypothesis edges awaiting
-- user confirmation. extract_claims (with the optional NLI classifier
-- installed) classifies each new claim against every existing hypothesis
-- and inserts SUPPORTS/CONTRADICTS candidates here as 'pending'; the
-- accept/reject routes (app layer) either commit a real
-- evidence_graph_edges row or mark the proposal rejected.
--
-- Event discipline (doc Section 10.4, option 2): resolving a proposed
-- edge does NOT emit a case_events row — proposed_edge_accepted /
-- proposed_edge_rejected are not in the EVENT_TYPES allow-list, and Part 2
-- deliberately deviates by logging at INFO level only instead of adding
-- new event types (schema change deferred to Part 4).
--
-- Preconditions: 012 (cases), 013 (evidence_graph_edges).
-- Idempotent: every statement carries an IF NOT EXISTS guard.
-- =====================================================================

CREATE TABLE IF NOT EXISTS proposed_edges (
    edge_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    from_node TEXT NOT NULL,
    from_type TEXT NOT NULL,
    to_node TEXT NOT NULL,
    to_type TEXT NOT NULL,
    relation TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    model_used TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
       CHECK (status IN ('pending', 'accepted', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    UNIQUE(case_id, from_node, to_node, relation)
);

CREATE INDEX IF NOT EXISTS idx_proposed_edges_case_status
    ON proposed_edges(case_id, status);