-- =====================================================================
-- 018_reasoning_loop.sql — close the investigation reasoning loop.
--
-- Hari ICE "addition and improvements" pass (see CHANGES.md):
--   Part A: actions.result_payload + actions.outcome  — executors return a
--           structured outcome; an action is no longer "completed" merely
--           because an executor returned an empty list.
--   Part B: hypothesis_predictions — every hypothesis can carry testable
--           predictions with resolution conditions (Metaculus-style
--           forecast records, kept strictly separate from confidence).
--   Part C: causal_edges — a causal explanation layer with provenance,
--           confidence, assumptions and temporal scope (proposed /
--           accepted / rejected lifecycle like proposed_edges).
-- Idempotent: guards every statement with IF NOT EXISTS / DO blocks.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Part A — actions.result_payload (JSONB) + actions.outcome (TEXT)
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'actions' AND column_name = 'result_payload'
    ) THEN
        ALTER TABLE actions ADD COLUMN result_payload JSONB NOT NULL DEFAULT '{}'::jsonb;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'actions' AND column_name = 'outcome'
    ) THEN
        ALTER TABLE actions ADD COLUMN outcome TEXT
            CHECK (outcome IN (
                'EVIDENCE_PRODUCED', 'INFORMATIVE_NO_EVIDENCE',
                'UNINFORMATIVE', 'UNSUPPORTED'
            ));
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- Part B — hypothesis_predictions
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hypothesis_predictions (
    prediction_id         TEXT PRIMARY KEY,
    case_id               TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    hypothesis_id         TEXT NOT NULL REFERENCES hypotheses(hypothesis_id) ON DELETE CASCADE,
    statement             TEXT NOT NULL,
    resolution_condition  TEXT NOT NULL,
    probability           DOUBLE PRECISION NOT NULL DEFAULT 0.5
                          CHECK (probability BETWEEN 0 AND 1),
    expected_resolution   TEXT,
    status                TEXT NOT NULL DEFAULT 'OPEN'
                          CHECK (status IN ('OPEN','CONFIRMED','REFUTED','UNRESOLVED','VOID')),
    observed_result       TEXT,
    score                 DOUBLE PRECISION CHECK (score BETWEEN 0 AND 1),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_predictions_case ON hypothesis_predictions(case_id, status);
CREATE INDEX IF NOT EXISTS idx_predictions_hypothesis ON hypothesis_predictions(hypothesis_id);

-- ---------------------------------------------------------------------
-- Part C — causal_edges (proposed causal relationships, human-reviewed)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS causal_edges (
    causal_edge_id    TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    cause_node_type   TEXT NOT NULL
                      CHECK (cause_node_type IN
                        ('event','component','variable','condition','hypothesis','claim')),
    cause_node_id     TEXT NOT NULL,
    cause_label       TEXT,
    effect_node_type  TEXT NOT NULL
                      CHECK (effect_node_type IN
                        ('event','component','variable','condition','hypothesis','claim')),
    effect_node_id    TEXT NOT NULL,
    effect_label      TEXT,
    relation          TEXT NOT NULL DEFAULT 'CONTRIBUTES_TO'
                      CHECK (relation IN
                        ('CAUSES','CONTRIBUTES_TO','PRECEDES','EXCLUDES','CORRELATES_WITH')),
    confidence        DOUBLE PRECISION NOT NULL DEFAULT 0.5
                      CHECK (confidence BETWEEN 0 AND 1),
    assumptions       TEXT[] NOT NULL DEFAULT '{}',
    valid_from        TIMESTAMPTZ,
    valid_until       TIMESTAMPTZ,
    provenance        JSONB NOT NULL DEFAULT '{}'::jsonb,
    proposed_by       TEXT NOT NULL DEFAULT 'ice'
                      CHECK (proposed_by IN ('ice','user','external_llm')),
    status            TEXT NOT NULL DEFAULT 'proposed'
                      CHECK (status IN ('proposed','accepted','rejected')),
    review_note       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at       TIMESTAMPTZ,
    UNIQUE (case_id, cause_node_type, cause_node_id, effect_node_type, effect_node_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_causal_edges_case ON causal_edges(case_id, status);
