-- =====================================================================
-- 013_evidence_graph.sql — ICE evidence graph edges
-- Owner: case_engine/evidence_graph.py
-- Purpose: typed, weighted cross-plane edges between evidence, claims,
--          hypotheses, gaps and actions.
-- Depends on: 012_case_model.sql (cases table).
-- =====================================================================

CREATE TABLE evidence_graph_edges (
    edge_id    TEXT PRIMARY KEY,
    case_id    TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    from_node  TEXT NOT NULL,
    from_type  TEXT NOT NULL CHECK (from_type IN
    ('evidence','claim','hypothesis','gap','action')),
    to_node    TEXT NOT NULL,
    to_type    TEXT NOT NULL CHECK (to_type IN ('claim','hypothesis','gap','action','evidence')),
    relation   TEXT NOT NULL CHECK (relation IN (
                   'SUPPORTS','CONTRADICTS','REFINES','SUPERSEDES',
                   'DERIVED_FROM','QUESTIONS','DISCRIMINATES','BLOCKS'
               )),
    weight     DOUBLE PRECISION NOT NULL DEFAULT 1.0
               CHECK (weight BETWEEN 0 AND 1),
    metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(from_node, to_node, relation)
);
CREATE INDEX idx_edges_from ON evidence_graph_edges(case_id, from_node, relation);
CREATE INDEX idx_edges_to ON evidence_graph_edges(case_id, to_node, relation);