"""Event timeline routes: jsonb payloads arrive from asyncpg as text."""
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit


def _row(payload):
    return {"event_id": "ev_1", "case_id": "case_1", "event_type": "case_created", "event_seq": 3,
            "conversation_turn": None, "wall_clock_time": datetime.now(timezone.utc), "payload": payload}


@pytest.mark.parametrize("payload,expected", [
    ('{"title": "x", "n": 2}', {"title": "x", "n": 2}),     # jsonb as text (the bug)
    ({"title": "x"}, {"title": "x"}),                        # already decoded
    ("not json", {"raw": "not json"}),
    ("[1, 2]", {"value": [1, 2]}),
])
def test_row_to_event_decodes_payload(payload, expected):
    from app.routes.replay import _row_to_event

    assert _row_to_event(_row(payload)).payload == expected


def test_after_seq_is_bounded():
    from app.main import app

    params = {p["name"]: p for p in app.openapi()["paths"]["/api/v1/cases/{case_id}/events"]["get"]["parameters"]}
    schema = params["after_seq"]["schema"]
    schema = next((s for s in schema.get("anyOf", [schema]) if s.get("type") == "integer"), schema)
    assert schema.get("maximum") == 2_147_483_647
