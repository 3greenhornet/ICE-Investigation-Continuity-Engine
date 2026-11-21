"""Unit tests for the decide() rule ladders (case_engine/decision.py).

No database, no LLM. Covers the debug-mode 6-rule confidence ladder and
the Part 2 §3 case_type branches (research / explore) by monkeypatching
every engine dependency decision.py imports at module scope
(get_case, list_hypotheses_for_case, get_current_version,
get_active_contradictions, generate_actions,
compute_priority_with_factors) plus the lazily-imported
information_gap.list_gaps_for_case. ``_make_decision`` (which persists)
is replaced by a recorder, so every test asserts on the exact
(decision_type, rationale, confidence, top_factors) the ladder WOULD
persist — the full DB round-trip is covered by the integration suite.

Engine surface under test (case_engine/decision.py):
    decide(case_id) -> Decision
        case_type 'research' -> _decide_research (4 rules),
        'explore'            -> _decide_explore (always INVESTIGATE),
        else                 -> _decide_debug (6-rule ladder).
"""
from datetime import datetime, timezone

import pytest

from case_engine import decision as dec
from case_engine import information_gap as gap_mod
from case_engine.decision import decide
from case_engine.schemas import Case, Contradiction, Decision, Hypothesis, HypothesisVersion, InformationGap

_NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _case(case_type: str = "debug", required_confidence: float = 0.85,
         case_id: str = "case_test") -> Case:
    return Case(
        case_id=case_id,
        title="Test",
        objective="Test objective",
        required_confidence=required_confidence,
        case_type=case_type,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _hypothesis(hid: str, statement: str = "H") -> Hypothesis:
    return Hypothesis(
        hypothesis_id=hid,
        case_id="case_test",
        statement=statement,
        normalized_statement=statement.lower(),
        created_at=_NOW,
    )


def _version(confidence: float) -> HypothesisVersion:
    return HypothesisVersion(
        version_id="hv_test",
        hypothesis_id="h_test",
        version_number=1,
        confidence=confidence,
        reason="test",
        cause_event_id="evt_test",
        created_at=_NOW,
    )


def _contradiction(severity: float) -> Contradiction:
    return Contradiction(
        contradiction_id=f"contra_{severity}",
        case_id="case_test",
        claim_a="c_a",
        claim_b="c_b",
        severity=severity,
        created_at=_NOW,
    )


def _gap(priority: float) -> InformationGap:
    return InformationGap(
        gap_id=f"gap_{priority}",
        case_id="case_test",
        needed_evidence="needed",
        why_needed="why",
        priority=priority,
        created_at=_NOW,
    )


def _const(value):
    async def _fn(*_args, **_kwargs):
        return value

    return _fn


class _DecisionRecorder:
    """Replaces decision._make_decision: records kwargs, returns a Decision."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, **kwargs) -> Decision:
        self.calls.append(kwargs)
        return Decision(
            decision_id=f"dec_{len(self.calls)}",
            case_id=kwargs["case_id"],
            decision_type=kwargs["decision_type"],
            recommended_action_id=kwargs.get("recommended_action_id"),
            rationale=kwargs["rationale"],
            confidence=kwargs["confidence"],
            top_factors=kwargs.get("top_factors") or {},
            created_at=_NOW,
        )

    @property
    def last(self) -> dict:
        return self.calls[-1]


@pytest.fixture
def recorder():
    return _DecisionRecorder()


@pytest.fixture
def wired(monkeypatch, recorder):
    """Patch decision.py's module-level deps to inert defaults."""
    monkeypatch.setattr(dec, "get_case", _const(_case()))
    monkeypatch.setattr(dec, "list_hypotheses_for_case", _const([]))
    monkeypatch.setattr(dec, "get_current_version", _const(_version(0.5)))
    monkeypatch.setattr(dec, "get_active_contradictions", _const([]))
    monkeypatch.setattr(dec, "generate_actions", _const([]))
    async def _fake_priority(_action):
        return 0.5, {"eig": 0.5}
    monkeypatch.setattr(dec, "compute_priority_with_factors", _fake_priority)
    monkeypatch.setattr(dec, "_make_decision", recorder)
    # _decide_research imports this lazily from case_engine.information_gap
    monkeypatch.setattr(gap_mod, "list_gaps_for_case", _const([]))
    return monkeypatch


def _hypotheses_with_versions(monkeypatch, confidences: list[float]):
    hypotheses = [_hypothesis(f"h{i}", f"Hypothesis {i}") for i in range(len(confidences))]
    monkeypatch.setattr(dec, "list_hypotheses_for_case", _const(hypotheses))
    versions = {h.hypothesis_id: _version(c) for h, c in zip(hypotheses, confidences)}

    async def fake_version(hid):
        return versions[hid]

    monkeypatch.setattr(dec, "get_current_version", fake_version)
    return hypotheses


# ---------------------------------------------------------------------------
# debug ladder
# ---------------------------------------------------------------------------

class TestDecideDebugLadder:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_no_hypotheses_returns_abstain(self, wired, recorder):
        decision = await decide("case_test")
        assert decision.decision_type == "ABSTAIN"
        assert recorder.last["confidence"] == 1.0
        assert recorder.last["top_factors"]["reason"] == "no_hypotheses"
        assert recorder.last["top_factors"]["case_type"] == "debug"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_no_leading_returns_abstain(self, wired, recorder, monkeypatch):
        # hypotheses exist but no version loads for any of them
        _hypotheses_with_versions(monkeypatch, [0.5])

        from case_engine.exceptions import HypothesisNotFoundError

        async def broken_version(_hid):
            raise HypothesisNotFoundError(_hid)

        monkeypatch.setattr(dec, "get_current_version", broken_version)
        decision = await decide("case_test")
        assert decision.decision_type == "ABSTAIN"
        assert recorder.last["confidence"] == 0.5
        assert recorder.last["top_factors"]["reason"] == "no_leading_hypothesis"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_high_confidence_no_contradictions_returns_act(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(required_confidence=0.85)))
        _hypotheses_with_versions(monkeypatch, [0.9, 0.3])
        monkeypatch.setattr(dec, "get_active_contradictions", _const([]))
        decision = await decide("case_test")
        assert decision.decision_type == "ACT"
        assert recorder.last["top_factors"]["leading_confidence"] == 0.9

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_high_confidence_with_decision_critical_returns_investigate(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(required_confidence=0.85)))
        _hypotheses_with_versions(monkeypatch, [0.9, 0.3])
        monkeypatch.setattr(
            dec,
            "get_active_contradictions",
            _const([_contradiction(0.9), _contradiction(0.2)]),
        )
        decision = await decide("case_test")
        assert decision.decision_type == "INVESTIGATE"
        assert recorder.last["top_factors"]["reason"] == "unresolved_contradictions"
        assert recorder.last["top_factors"]["contradiction_count"] == 1  # only >0.7

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_low_confidence_no_actions_returns_abstain(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(required_confidence=0.85)))
        _hypotheses_with_versions(monkeypatch, [0.4])
        monkeypatch.setattr(dec, "generate_actions", _const([]))
        decision = await decide("case_test")
        assert decision.decision_type == "ABSTAIN"
        assert recorder.last["top_factors"]["reason"] == "no_actions_available"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_low_confidence_best_priority_below_threshold_returns_abstain(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(required_confidence=0.85)))
        _hypotheses_with_versions(monkeypatch, [0.4])
        monkeypatch.setattr(
            dec, "generate_actions", _const([_fake_action("act_1"), _fake_action("act_2")])
        )

        async def low_priority(_action):
            return 0.05, {"eig": 0.05}

        monkeypatch.setattr(dec, "compute_priority_with_factors", low_priority)
        decision = await decide("case_test")
        assert decision.decision_type == "ABSTAIN"
        assert recorder.last["top_factors"]["best_priority"] == 0.05
        assert "below actionability" in recorder.last["rationale"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_low_confidence_best_priority_above_threshold_returns_investigate(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(required_confidence=0.85)))
        _hypotheses_with_versions(monkeypatch, [0.4])
        actions = [_fake_action("act_best", priority=0.6), _fake_action("act_worse", priority=0.2)]
        monkeypatch.setattr(dec, "generate_actions", _const(actions))

        async def scored(action):
            value = 0.6 if action.action_id == "act_best" else 0.2
            return value, {"eig": value}

        monkeypatch.setattr(dec, "compute_priority_with_factors", scored)
        decision = await decide("case_test")
        assert decision.decision_type == "INVESTIGATE"
        assert decision.recommended_action_id == "act_best"
        assert recorder.last["top_factors"]["gap_to_threshold"] == pytest.approx(0.45)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_all_priorities_fail_returns_abstain(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(required_confidence=0.85)))
        _hypotheses_with_versions(monkeypatch, [0.4])
        monkeypatch.setattr(dec, "generate_actions", _const([_fake_action("act_1")]))

        async def exploding_priority(_action):
            raise RuntimeError("scorer down")

        monkeypatch.setattr(dec, "compute_priority_with_factors", exploding_priority)
        decision = await decide("case_test")
        assert decision.decision_type == "ABSTAIN"
        assert recorder.last["top_factors"]["reason"] == "priority_computation_failed"


def _fake_action(action_id: str, priority: float = 0.5):
    """A light Action stand-in for the debug ladder's rule 5/6 tests."""
    from types import SimpleNamespace

    return SimpleNamespace(
        action_id=action_id,
        action_type="RETRIEVE",
        target="fetch logs",
        priority=priority,
    )


# ---------------------------------------------------------------------------
# research mode (Part 2 §3.2)
# ---------------------------------------------------------------------------

class TestDecideResearchMode:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_research_no_hypotheses_returns_investigate(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(case_type="research")))
        monkeypatch.setattr(dec, "list_hypotheses_for_case", _const([]))
        decision = await decide("case_test")
        assert decision.decision_type == "INVESTIGATE"
        assert recorder.last["top_factors"]["reason"] == "no_hypotheses"
        assert recorder.last["top_factors"]["case_type"] == "research"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_research_active_contradiction_returns_investigate(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(case_type="research")))
        _hypotheses_with_versions(monkeypatch, [0.5, 0.4])
        monkeypatch.setattr(
            dec, "get_active_contradictions", _const([_contradiction(0.4)])
        )
        decision = await decide("case_test")
        assert decision.decision_type == "INVESTIGATE"
        assert recorder.last["top_factors"]["reason"] == "active_contradictions"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_research_high_priority_gap_returns_investigate(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(case_type="research")))
        _hypotheses_with_versions(monkeypatch, [0.5, 0.4])
        monkeypatch.setattr(dec, "get_active_contradictions", _const([]))
        monkeypatch.setattr(
            gap_mod, "list_gaps_for_case", _const([_gap(0.9), _gap(0.2)])
        )
        decision = await decide("case_test")
        assert decision.decision_type == "INVESTIGATE"
        assert recorder.last["top_factors"]["reason"] == "high_priority_gaps"
        assert recorder.last["top_factors"]["gap_count"] == 1  # only > 0.5

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_research_no_contradictions_no_gaps_returns_act(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(case_type="research")))
        _hypotheses_with_versions(monkeypatch, [0.5, 0.4])
        monkeypatch.setattr(dec, "get_active_contradictions", _const([]))
        monkeypatch.setattr(gap_mod, "list_gaps_for_case", _const([_gap(0.2)]))
        decision = await decide("case_test")
        assert decision.decision_type == "ACT"
        assert recorder.last["top_factors"]["reason"] == "ready_to_synthesize"
        assert recorder.last["confidence"] == 1.0


# ---------------------------------------------------------------------------
# explore mode (Part 2 §3.2)
# ---------------------------------------------------------------------------

class TestDecideExploreMode:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_explore_always_investigate_no_hypotheses(
        self, wired, recorder, monkeypatch
    ):
        monkeypatch.setattr(dec, "get_case", _const(_case(case_type="explore")))
        monkeypatch.setattr(dec, "list_hypotheses_for_case", _const([]))
        decision = await decide("case_test")
        assert decision.decision_type == "INVESTIGATE"
        # Hari ICE improvements pass: the explore ladder's Rule 1 has its
        # own reason code now (the old stub always said "explore_mode").
        assert recorder.last["top_factors"]["reason"] == "no_direction"
        assert "no direction" in decision.rationale

    # Reclassified unit -> integration (2026-11-16 hardening pass): this
    # test's outcome depends on decide()'s workspace/recency passes, which
    # read real database state — it now runs against a live pool. Its
    # historical green run was an accident: a half-closed pool made the
    # recency query THROW, which silently selected Rule 4. The test now
    # sets up Rule 4's precondition EXPLICITLY (hypotheses + recent
    # movement), and the sibling test below pins Rule 3 (no recent
    # movement -> ABSTAIN) so the full ladder contract is covered.
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_decide_explore_rule4_investigate_with_recent_movement(
        self, db_pool, wired, recorder, monkeypatch
    ):
        from case_engine.case import create_case

        case = await create_case("Explore probe", "objective")
        monkeypatch.setattr(
            dec, "get_case", _const(_case(case_type="explore", case_id=case.case_id))
        )
        _hypotheses_with_versions(monkeypatch, [0.9, 0.1])
        # Rule 4 precondition: recent movement on this case (within 7 days).
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO case_events (event_id, case_id, event_type, "
                "event_seq, wall_clock_time, payload) VALUES "
                "($1, $2, 'evidence_ingested', 747, NOW(), '{}'::jsonb)",
                f"evt_probe_{case.case_id}", case.case_id,
            )
        # even a decisive confidence cannot ACT in explore mode
        decision = await decide(case.case_id)
        assert decision.decision_type == "INVESTIGATE"
        assert recorder.last["top_factors"]["case_type"] == "explore"
        assert "user drives resolution" in decision.rationale

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_decide_explore_rule3_abstain_when_stale(
        self, db_pool, wired, recorder, monkeypatch
    ):
        """Rule 3: hypotheses exist but nothing new has arrived for 7 days
        and the workspace has no candidates -> ABSTAIN with an explicit
        stale-exploration rationale (explore mode must notice its own dead
        ends instead of looping INVESTIGATE forever)."""
        from case_engine.case import create_case

        case = await create_case("Explore stale probe", "objective")
        monkeypatch.setattr(
            dec, "get_case", _const(_case(case_type="explore", case_id=case.case_id))
        )
        _hypotheses_with_versions(monkeypatch, [0.9, 0.1])
        # deliberately NO recent event: the case is stale
        decision = await decide(case.case_id)
        assert decision.decision_type == "ABSTAIN"
        assert recorder.last["top_factors"]["reason"] == "stale_exploration"
        assert "Consider resolving, archiving" in decision.rationale