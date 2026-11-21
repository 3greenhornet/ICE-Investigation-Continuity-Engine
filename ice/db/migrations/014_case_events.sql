-- =====================================================================
-- 014_case_events.sql — ICE append-only event log
-- Owner: case_engine/__init__.py::_emit_event (sole writer)
-- Purpose: durable, replayable audit trail; one event per state mutation.
-- Depends on: 012_case_model.sql (cases table).
-- =====================================================================

CREATE TABLE case_events (
    event_id          TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    event_type        TEXT NOT NULL,
    event_seq         INTEGER NOT NULL,
    conversation_turn INTEGER,
    wall_clock_time   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload           JSONB NOT NULL,
    UNIQUE(case_id, event_seq)
);
CREATE INDEX idx_events_case ON case_events(case_id, event_seq);
CREATE INDEX idx_events_type ON case_events(case_id, event_type);