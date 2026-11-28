-- 023_lab_propose_action.sql — allow LAB_PROPOSE in actions.action_type CHECK constraint (idempotent).
--
-- Supports ICE proposing reversible sandbox Improvement Lab experiments
-- (never auto-applying edits; human approval remains required).

DO $$
DECLARE
    con_name TEXT;
BEGIN
    SELECT c.conname INTO con_name
    FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    WHERE t.relname = 'actions'
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) ILIKE '%action_type%'
    LIMIT 1;

    IF con_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE actions DROP CONSTRAINT %I', con_name);
    END IF;

    ALTER TABLE actions
        ADD CONSTRAINT actions_action_type_check
        CHECK (action_type IN (
            'SEARCH','RETRIEVE','COMPARE','INSPECT',
            'RUN_TEST','ASK_HUMAN','REVISIT_HYPOTHESIS',
            'FIND_COUNTEREVIDENCE','WAIT','ABSTAIN','LAB_PROPOSE'
        ));
END $$;
