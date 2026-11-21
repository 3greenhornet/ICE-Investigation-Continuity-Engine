-- =====================================================================
-- 021_project_goals.sql — the Evolving Project Model (goal system).
--
-- Implements the document's central late-phase requirement: ICE must
-- understand a project's INTENTION, not only its artifacts — starting
-- from the first vague conversation (goal clarity Level 1) through
-- defined engineering objectives (Level 4), and must continuously
-- reconcile that intention against implementation evidence.
--
--   Part A: project_goals — typed intent nodes (the document's object
--           table: goal / need / actor / scenario / requirement /
--           quality_objective / constraint / assumption / decision /
--           alternative / risk / open_question / milestone) with:
--             * clarity_level 1-4 (vague intention -> defined objective)
--             * origin = the epistemic category the document demands:
--               developer_statement / developer_confirmed / ice_inference
--               / ice_proposal / repo_observation — an inference is NEVER
--               silently convertible into an accepted decision
--             * version + supersedes/superseded_by: revised intentions
--               SUPERSEDE their predecessors, never overwrite them
--   Part B: goal_edges — typed relationships (refines, depends_on,
--           constrains, motivated_by, addresses, conflicts_with,
--           supersedes, derived_from, challenges) so "why does this
--           component exist / which goals depend on it" is answerable.
--   Part C: goal_progress — implementation maturity per goal, tracked
--           separately from verification, with the document's honest
--           state ladder (no_candidate / scaffolded / candidate /
--           connected / tested / verified / runtime_observed) and the
--           evidence_ids backing each assessment.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Part A — project_goals (typed intent nodes, versioned, superseding)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_goals (
    goal_id        TEXT PRIMARY KEY,
    case_id        TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    kind           TEXT NOT NULL DEFAULT 'goal'
                   CHECK (kind IN (
                       'goal','need','actor','scenario','requirement',
                       'quality_objective','constraint','assumption',
                       'decision','alternative','risk','open_question',
                       'milestone'
                   )),
    statement      TEXT NOT NULL,
    clarity_level  INTEGER NOT NULL DEFAULT 1
                   CHECK (clarity_level BETWEEN 1 AND 4),
    -- 1 exploratory/vague · 2 emerging direction · 3 partially specified
    -- 4 defined engineering objective
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN (
                       'exploring','active','satisfied','abandoned',
                       'superseded'
                   )),
    origin         TEXT NOT NULL DEFAULT 'developer_statement'
                   CHECK (origin IN (
                       'developer_statement','developer_confirmed',
                       'ice_inference','ice_proposal','repo_observation'
                   )),
    rationale      TEXT,
    version        INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    supersedes     TEXT,
    superseded_by  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_project_goals_case
    ON project_goals(case_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_project_goals_origin
    ON project_goals(case_id, origin);

-- ---------------------------------------------------------------------
-- Part B — goal_edges (typed relationships between intent nodes)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS goal_edges (
    edge_id    TEXT PRIMARY KEY,
    case_id    TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    from_goal  TEXT NOT NULL REFERENCES project_goals(goal_id) ON DELETE CASCADE,
    to_goal    TEXT NOT NULL REFERENCES project_goals(goal_id) ON DELETE CASCADE,
    relation   TEXT NOT NULL CHECK (relation IN (
                   'refines','depends_on','constrains','motivated_by',
                   'addresses','conflicts_with','supersedes','derived_from',
                   'challenges'
               )),
    provenance TEXT NOT NULL DEFAULT 'ice_inference'
               CHECK (provenance IN (
                   'developer_statement','developer_confirmed',
                   'ice_inference','ice_proposal','repo_observation'
               )),
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (from_goal, to_goal, relation)
);
CREATE INDEX IF NOT EXISTS idx_goal_edges_case ON goal_edges(case_id, relation);

-- ---------------------------------------------------------------------
-- Part C — goal_progress (implementation maturity, evidence-backed)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS goal_progress (
    progress_id  TEXT PRIMARY KEY,
    goal_id      TEXT NOT NULL REFERENCES project_goals(goal_id) ON DELETE CASCADE,
    case_id      TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    -- the document's maturity ladder: a class existing does not prove a
    -- feature works; a test file existing does not prove a test passed.
    maturity     TEXT NOT NULL DEFAULT 'no_candidate'
                 CHECK (maturity IN (
                     'no_candidate','scaffolded','candidate','connected',
                     'tested','verified','runtime_observed'
                 )),
    evidence_ids TEXT[] NOT NULL DEFAULT '{}',
    assessed_by  TEXT NOT NULL DEFAULT 'ice'
                 CHECK (assessed_by IN ('user','ice')),
    note         TEXT,
    assessed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_goal_progress_goal
    ON goal_progress(goal_id, assessed_at DESC);
