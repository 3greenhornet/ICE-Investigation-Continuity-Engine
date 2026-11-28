-- 022_goal_attributes.sql — customisable goal attributes (idempotent).
--
-- The Evolving Project Model (021) fixes the vocabulary (kinds, statuses,
-- origins, relations) on purpose; what it lacked was room for the user's own
-- planning data. These columns are additive and optional:
--   priority          1 (highest) .. 5 (lowest); NULL = unprioritised
--   success_criteria  how the user will know the goal is met (free text)
--   tags              free labels for filtering/grouping
--   target_date       optional deadline
--   metadata          anything else (team, link to ticket, budget, ...)
-- Changing them on an existing goal goes through supersession (a new version),
-- exactly like a statement change, so history is preserved and the goal
-- plane keeps emitting only allow-listed events (goal_created).

ALTER TABLE project_goals ADD COLUMN IF NOT EXISTS priority SMALLINT;
ALTER TABLE project_goals ADD COLUMN IF NOT EXISTS success_criteria TEXT;
ALTER TABLE project_goals ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE project_goals ADD COLUMN IF NOT EXISTS target_date DATE;
ALTER TABLE project_goals ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'project_goals_priority_range'
    ) THEN
        ALTER TABLE project_goals
            ADD CONSTRAINT project_goals_priority_range
            CHECK (priority IS NULL OR priority BETWEEN 1 AND 5);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_project_goals_tags ON project_goals USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_project_goals_priority ON project_goals(case_id, priority);
