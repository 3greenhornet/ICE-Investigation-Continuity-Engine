-- =====================================================================
-- 012_case_model.sql — ICE (Investigation Continuity Engine) case model
-- Owner: case_engine/
-- Purpose: root object graph — cases, sources, evidence, claims,
--          hypotheses, hypothesis_versions, contradictions,
--          information_gaps, investigation_threads, actions, decisions.
-- Depends on: pgvector extension (vector type) + legacy schema (001-011).
--
-- NOTE (merge spec Part 1 §5.8 P8): This migration drops the legacy Hari
-- `hypotheses`, `hypothesis_versions`, and `contradictions` tables, whose
-- names collide with the ICE tables created below. The sibling
-- 012a_safety_check.sql (which MUST run first) aborts if any of those
-- tables contains data. If 012a passes (empty tables), this migration
-- safely drops the empty shells and creates the ICE tables.
--
-- Audit-mandated additions beyond the ICE spec §5.1 text (worklog ice-0):
--   * evidence.metadata JSONB NOT NULL DEFAULT '{}'::jsonb
--     (audit gap #1/#19: ingest_evidence accepts metadata but the table
--      lacked a column for it)
--   * CREATE INDEX idx_evidence_case_active ON evidence(case_id, status);
--     (spec already carries the same key set as idx_evidence_case; kept
--      under both names for audit/task reference compatibility)
-- =====================================================================

DROP TABLE IF EXISTS contradictions CASCADE;
DROP TABLE IF EXISTS hypothesis_versions CASCADE;
DROP TABLE IF EXISTS hypotheses CASCADE;

-- =====================================================================
-- cases: the root object that survives sessions
-- =====================================================================
CREATE TABLE cases (
    case_id                TEXT PRIMARY KEY,
    title                  TEXT NOT NULL,
    objective              TEXT NOT NULL,
    decision_deadline      TIMESTAMPTZ,
    required_confidence    DOUBLE PRECISION NOT NULL DEFAULT 0.85
                           CHECK (required_confidence >= 0.0 AND required_confidence <= 1.0),
    cost_budget_tool_calls INTEGER NOT NULL DEFAULT 100
                           CHECK (cost_budget_tool_calls > 0),
    cost_budget_tokens     INTEGER NOT NULL DEFAULT 500000
                           CHECK (cost_budget_tokens > 0),
    status                 TEXT NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open','investigating','deciding','resolved','abandoned')),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_cases_status ON cases(status);
CREATE INDEX idx_cases_updated ON cases(updated_at DESC);

-- =====================================================================
-- sources: provenance roots
-- =====================================================================
CREATE TABLE sources (
    source_id            TEXT PRIMARY KEY,
    case_id              TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    source_type          TEXT NOT NULL
                         CHECK (source_type IN
    ('DOCUMENT','TOOL','HUMAN','URL','DATABASE','LOG_FILE','CODE_REPO')),
    source_uri           TEXT,
    source_hash          TEXT,
    parent_source_id     TEXT REFERENCES sources(source_id),
    lineage_id           TEXT,
    duplicate_cluster_id TEXT,
    authority_score      DOUBLE PRECISION NOT NULL DEFAULT 0.5
                         CHECK (authority_score BETWEEN 0 AND 1),
    directness_score     DOUBLE PRECISION NOT NULL DEFAULT 0.5
                         CHECK (directness_score BETWEEN 0 AND 1),
    freshness_score      DOUBLE PRECISION NOT NULL DEFAULT 0.5
                         CHECK (freshness_score BETWEEN 0 AND 1),
    independence_score   DOUBLE PRECISION NOT NULL DEFAULT 0.5
                         CHECK (independence_score BETWEEN 0 AND 1),
    metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_sources_case ON sources(case_id);
CREATE INDEX idx_sources_lineage ON sources(lineage_id);
CREATE INDEX idx_sources_cluster ON sources(duplicate_cluster_id);

-- =====================================================================
-- evidence: the atomic fact
-- =====================================================================
CREATE TABLE evidence (
    evidence_id          TEXT PRIMARY KEY,
    case_id              TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    source_id            TEXT NOT NULL REFERENCES sources(source_id),
    evidence_type        TEXT NOT NULL
                         CHECK (evidence_type IN (
                             'OBSERVATION','MEASUREMENT','DOCUMENT_CLAIM',
                             'HUMAN_ASSERTION','TOOL_RESULT','EXPERIMENT_RESULT',
                             'MODEL_INFERENCE','MODEL_SUMMARY'
                         )),
    content              TEXT NOT NULL,
    embedding            vector(768),
    observed_at          TIMESTAMPTZ,
    recorded_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_from           TIMESTAMPTZ,
    valid_until          TIMESTAMPTZ,
    supersedes           TEXT REFERENCES evidence(evidence_id),
    superseded_by        TEXT REFERENCES evidence(evidence_id),
    source_lineage_id    TEXT,
    duplicate_cluster_id TEXT,
    quality_score        DOUBLE PRECISION NOT NULL DEFAULT 0.5
                         CHECK (quality_score BETWEEN 0 AND 1),
    status               TEXT NOT NULL DEFAULT 'ACTIVE'
                         CHECK (status IN
    ('ACTIVE','STALE','SUPERSEDED','DISPUTED','RESOLVED','ARCHIVED')),
    -- audit gap #1/#19 fix: ingest_evidence accepts metadata; persist it
    metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,
    extracted_claim_ids  TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_evidence_case ON evidence(case_id, status);
CREATE INDEX idx_evidence_case_active ON evidence(case_id, status);
CREATE INDEX idx_evidence_validity ON evidence(case_id, valid_from, valid_until);
CREATE INDEX idx_evidence_lineage ON evidence(source_lineage_id);
CREATE INDEX idx_evidence_cluster ON evidence(duplicate_cluster_id);
CREATE INDEX idx_evidence_embedding ON evidence USING hnsw (embedding
    vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- =====================================================================
-- claims: the middle layer between evidence and hypothesis
-- =====================================================================
CREATE TABLE claims (
    claim_id                   TEXT PRIMARY KEY,
    case_id                    TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    statement                  TEXT NOT NULL,
    claim_type                 TEXT NOT NULL
                               CHECK (claim_type IN (
                                   'MEASUREMENT','OBSERVATION','CORRELATION',
                                   'TEMPORAL_ASSOCIATION','CAUSAL_CLAIM',
                                   'INTERPRETATION','PREDICTION','ASSUMPTION'
                               )),
    scope                      JSONB,
    valid_from                 TIMESTAMPTZ,
    valid_until                TIMESTAMPTZ,
    supporting_evidence_ids    TEXT[] NOT NULL DEFAULT '{}',
    contradicting_evidence_ids TEXT[] NOT NULL DEFAULT '{}',
    confidence                 DOUBLE PRECISION NOT NULL DEFAULT 0.5
                               CHECK (confidence BETWEEN 0 AND 1),
    status                     TEXT NOT NULL DEFAULT 'ACTIVE'
                               CHECK (status IN ('ACTIVE','SUPERSEDED','RETRACTED')),
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_claims_case ON claims(case_id, claim_type);

-- =====================================================================
-- hypotheses: versioned belief identity
-- =====================================================================
CREATE TABLE hypotheses (
    hypothesis_id        TEXT PRIMARY KEY,
    case_id              TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    statement            TEXT NOT NULL,
    normalized_statement TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'PROPOSED'
                         CHECK (status IN (
                             'PROPOSED','SUPPORTED','CHALLENGED',
                             'SURVIVED','FALSIFIED','CONFIRMED','REMAINS_OPEN'
                         )),
    current_version_id   TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(case_id, normalized_statement)
);
CREATE INDEX idx_hypotheses_case ON hypotheses(case_id, status);

-- =====================================================================
-- hypothesis_versions: versioned belief state
-- =====================================================================
CREATE TABLE hypothesis_versions (
    version_id                   TEXT PRIMARY KEY,
    hypothesis_id                TEXT NOT NULL REFERENCES hypotheses(hypothesis_id) ON DELETE CASCADE,
    version_number               INTEGER NOT NULL CHECK (version_number >= 1),
    confidence                   DOUBLE PRECISION NOT NULL
                                 CHECK (confidence BETWEEN 0 AND 1),
    prior_confidence             DOUBLE PRECISION,
    supporting_claim_ids         TEXT[] NOT NULL DEFAULT '{}',
    contradicting_claim_ids      TEXT[] NOT NULL DEFAULT '{}',
    supporting_evidence_added    TEXT[] NOT NULL DEFAULT '{}',
    supporting_evidence_removed  TEXT[] NOT NULL DEFAULT '{}',
    contradicting_evidence_added TEXT[] NOT NULL DEFAULT '{}',
    reason                       TEXT NOT NULL,
    cause_event_id               TEXT NOT NULL,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(hypothesis_id, version_number)
);
CREATE INDEX idx_hypothesis_versions_hyp ON hypothesis_versions(hypothesis_id,
    version_number DESC);

-- =====================================================================
-- contradictions: detected conflict between two claims
-- =====================================================================
CREATE TABLE contradictions (
    contradiction_id       TEXT PRIMARY KEY,
    case_id                TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    claim_a                TEXT NOT NULL REFERENCES claims(claim_id),
    claim_b                TEXT NOT NULL REFERENCES claims(claim_id),
    scope_a                JSONB,
    scope_b                JSONB,
    time_a                 TIMESTAMPTZ,
    time_b                 TIMESTAMPTZ,
    relation_type          TEXT NOT NULL DEFAULT 'contradiction'
                           CHECK (relation_type IN (
                               'contradiction','refinement','scope_mismatch',
                               'temporal_mismatch','independent'
                           )),
    severity               DOUBLE PRECISION NOT NULL DEFAULT 0.5
                           CHECK (severity BETWEEN 0 AND 1),
    source_evidence_a      TEXT REFERENCES evidence(evidence_id),
    source_evidence_b      TEXT REFERENCES evidence(evidence_id),
    status                 TEXT NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active','resolving','resolved','archived')),
    resolution_summary     TEXT,
    resolution_evidence_id TEXT REFERENCES evidence(evidence_id),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at            TIMESTAMPTZ
);
CREATE INDEX idx_contradictions_case ON contradictions(case_id, status);

-- =====================================================================
-- information_gaps: explicit uncertainty
-- =====================================================================
CREATE TABLE information_gaps (
    gap_id                        TEXT PRIMARY KEY,
    case_id                       TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    needed_evidence               TEXT NOT NULL,
    current_state                 TEXT,
    why_needed                    TEXT,
    priority                      DOUBLE PRECISION NOT NULL DEFAULT 0.5
                                  CHECK (priority BETWEEN 0 AND 1),
    discriminating_hypothesis_ids TEXT[] NOT NULL DEFAULT '{}',
    depends_on_gap_ids            TEXT[] NOT NULL DEFAULT '{}',
    blocks_action_ids             TEXT[] NOT NULL DEFAULT '{}',
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_by_evidence_ids      TEXT[] NOT NULL DEFAULT '{}',
    resolved_at                   TIMESTAMPTZ
);
CREATE INDEX idx_gaps_case ON information_gaps(case_id, priority DESC);

-- =====================================================================
-- investigation_threads: persistent unresolved work (from PursuitRegistry)
-- =====================================================================
CREATE TABLE investigation_threads (
    thread_id             TEXT PRIMARY KEY,
    case_id               TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    question              TEXT NOT NULL,
    origin                TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_progress_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    strength              DOUBLE PRECISION NOT NULL DEFAULT 0.5
                          CHECK (strength BETWEEN 0 AND 1),
    decision_impact       DOUBLE PRECISION NOT NULL DEFAULT 0.5
                          CHECK (decision_impact BETWEEN 0 AND 1),
    uncertainty           DOUBLE PRECISION NOT NULL DEFAULT 0.5
                          CHECK (uncertainty BETWEEN 0 AND 1),
    blocking_question_id  TEXT REFERENCES information_gaps(gap_id),
    reinforcement_history INTEGER[] NOT NULL DEFAULT '{}',
    is_active             BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX idx_threads_case ON investigation_threads(case_id, is_active, strength
    DESC);

-- =====================================================================
-- actions: investigation steps
-- =====================================================================
CREATE TABLE actions (
    action_id                 TEXT PRIMARY KEY,
    case_id                   TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    action_type               TEXT NOT NULL
                              CHECK (action_type IN (
                                  'SEARCH','RETRIEVE','COMPARE','INSPECT',
                                  'RUN_TEST','ASK_HUMAN','REVISIT_HYPOTHESIS',
                                  'FIND_COUNTEREVIDENCE','WAIT','ABSTAIN'
                              )),
    target                    TEXT,
    expected_outcome          TEXT,
    cost_estimate             DOUBLE PRECISION NOT NULL DEFAULT 0.5
                              CHECK (cost_estimate >= 0),
    risk                      DOUBLE PRECISION NOT NULL DEFAULT 0.0
                              CHECK (risk BETWEEN 0 AND 1),
    expected_information_gain DOUBLE PRECISION NOT NULL DEFAULT 0.5
                              CHECK (expected_information_gain BETWEEN 0 AND 1),
    decision_impact           DOUBLE PRECISION NOT NULL DEFAULT 0.5
                              CHECK (decision_impact BETWEEN 0 AND 1),
    falsification_value       DOUBLE PRECISION NOT NULL DEFAULT 0.0
                              CHECK (falsification_value BETWEEN 0 AND 1),
    approval_required         BOOLEAN NOT NULL DEFAULT FALSE,
    approval_class            TEXT NOT NULL DEFAULT 'READ_ONLY'
                              CHECK (approval_class IN
    ('READ_ONLY','REVERSIBLE','IRREVERSIBLE','EXTERNAL_SIDE_EFFECT')),
    status                    TEXT NOT NULL DEFAULT 'proposed'
                              CHECK (status IN
    ('proposed','approved','executing','completed','failed','cancelled','rejected')),
    result_evidence_ids       TEXT[] NOT NULL DEFAULT '{}',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at              TIMESTAMPTZ
);
CREATE INDEX idx_actions_case ON actions(case_id, status);

-- =====================================================================
-- decisions: output of an investigation cycle
-- =====================================================================
CREATE TABLE decisions (
    decision_id             TEXT PRIMARY KEY,
    case_id                 TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    decision_type           TEXT NOT NULL
                            CHECK (decision_type IN ('ACT','INVESTIGATE','ABSTAIN')),
    recommended_action_id   TEXT REFERENCES actions(action_id),
    rationale               TEXT NOT NULL,
    confidence              DOUBLE PRECISION NOT NULL DEFAULT 0.5
                            CHECK (confidence BETWEEN 0 AND 1),
    alternatives_considered JSONB NOT NULL DEFAULT '[]'::jsonb,
    top_factors             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_decisions_case ON decisions(case_id, created_at DESC);