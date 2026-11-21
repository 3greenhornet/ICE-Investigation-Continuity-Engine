-- =====================================================================
-- 019_plan_and_workspace.sql — adaptive plan management + investigative
-- workspace (Hari ICE improvements pass).
--
--   Part A: plan_items / plan_revisions — the adaptive plan & checklist
--           manager. Original steps are SUPERSEDED, never deleted; every
--           revision is recorded with its reason and who decided.
--   Part B: workspace_items — persistent attention candidates with
--           surfacing history (drives semantic-refractory decay so the
--           same thought is not resurfaced every cycle).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Part A — plan_items + plan_revisions
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plan_items (
    plan_item_id      TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    objective_ref     TEXT,
    description       TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'not_started'
                      CHECK (status IN (
                          'not_started','in_progress','blocked','completed',
                          'completed_unverified','superseded','deferred','rejected'
                      )),
    depends_on        TEXT[] NOT NULL DEFAULT '{}',
    evidence_ids      TEXT[] NOT NULL DEFAULT '{}',
    version           INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    supersedes_item   TEXT,
    superseded_by     TEXT,
    completion_note   TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_plan_items_case ON plan_items(case_id, status);

CREATE TABLE IF NOT EXISTS plan_revisions (
    revision_id     TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    from_version    INTEGER,
    to_version      INTEGER NOT NULL,
    reason          TEXT NOT NULL,
    evaluation      TEXT,
    decided_by      TEXT NOT NULL DEFAULT 'ice'
                    CHECK (decided_by IN ('user','ice')),
    affected_items  TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_plan_revisions_case ON plan_revisions(case_id, created_at DESC);

-- ---------------------------------------------------------------------
-- Part B — workspace_items (persistent attention candidates)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspace_items (
    workspace_item_id  TEXT PRIMARY KEY,
    case_id            TEXT REFERENCES cases(case_id) ON DELETE CASCADE,
    candidate_key      TEXT NOT NULL,
    category           TEXT NOT NULL DEFAULT 'immediate'
                       CHECK (category IN
                         ('immediate','maintenance','development','exploration','speculation')),
    item_type          TEXT NOT NULL DEFAULT 'thought',
    content            TEXT NOT NULL,
    score              DOUBLE PRECISION NOT NULL DEFAULT 0.5
                       CHECK (score BETWEEN 0 AND 1),
    factors            JSONB NOT NULL DEFAULT '{}'::jsonb,
    disposition        TEXT NOT NULL DEFAULT 'active'
                       CHECK (disposition IN
                         ('active','observe_only','actioned','dismissed','dormant')),
    surfacing_count    INTEGER NOT NULL DEFAULT 0,
    last_surfaced_at   TIMESTAMPTZ,
    origin             TEXT NOT NULL DEFAULT 'endogenous',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (candidate_key)
);
CREATE INDEX IF NOT EXISTS idx_workspace_items_case ON workspace_items(case_id, disposition);
CREATE INDEX IF NOT EXISTS idx_workspace_items_active ON workspace_items(disposition, score DESC);
