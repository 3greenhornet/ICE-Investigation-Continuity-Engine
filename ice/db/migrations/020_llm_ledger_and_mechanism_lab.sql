-- =====================================================================
-- 020_llm_ledger_and_mechanism_lab.sql — reproducibility + research ledger.
--
--   Part A: llm_ledger — one row per LLM operation: prompt id/version,
--           role, model, request/response hashes, token estimates,
--           latency, success. The "prompt reproducibility" and
--           "cost measurement" gaps from the second-pass audit.
--   Part B: mechanism_lab — the Mechanism Laboratory catalogue: external
--           mechanisms worth borrowing, our inversion, experiments and
--           adopt/adapt/reject decisions (seeded from the Hari ICE
--           improvements document's donor list by case_engine.mechanism_lab).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Part A — llm_ledger
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_ledger (
    ledger_id       TEXT PRIMARY KEY,
    case_id         TEXT REFERENCES cases(case_id) ON DELETE CASCADE,
    operation       TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'GENERATOR'
                    CHECK (role IN
                      ('GENERATOR','CRITIC','RANKER','EXTRACTOR','GENERAL')),
    prompt_id       TEXT NOT NULL DEFAULT 'adhoc',
    prompt_version  TEXT NOT NULL DEFAULT 'v0',
    model           TEXT NOT NULL,
    request_hash    TEXT,
    response_hash   TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    latency_ms      DOUBLE PRECISION,
    success         BOOLEAN NOT NULL DEFAULT TRUE,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_llm_ledger_case ON llm_ledger(case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_ledger_operation ON llm_ledger(operation, created_at DESC);

-- ---------------------------------------------------------------------
-- Part B — mechanism_lab
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mechanism_lab (
    mechanism_id      TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    original_domain   TEXT NOT NULL,
    original_problem  TEXT,
    source_ref        TEXT,
    borrowed_principle TEXT NOT NULL,
    inverted_use      TEXT NOT NULL,
    adaptations       TEXT[] NOT NULL DEFAULT '{}',
    experiment_status TEXT NOT NULL DEFAULT 'proposed'
                      CHECK (experiment_status IN
                        ('proposed','running','evaluated','abandoned')),
    evaluation        TEXT,
    resource_profile  JSONB NOT NULL DEFAULT '{}'::jsonb,
    decision          TEXT NOT NULL DEFAULT 'pending'
                      CHECK (decision IN
                        ('pending','reuse','adapt','reject','defer','revisit')),
    decision_note     TEXT,
    ice_module        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
