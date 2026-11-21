"""Unit tests for the next-best-action scoring formulas (case_engine/next_best_action.py).

No database (all loaders monkeypatched in next_best_action's namespace),
no LLM. Covers the action-value scoring stack: EIG (entropy x
discrimination), decision impact, falsification value, and the composite
priority with its mandatory factor breakdown (Part 3 §0 rule 11),
including the Part 2 §8 thread-debt bonus.

Engine surface under test:
    compute_eig(action)                     — <2 hypotheses -> 0.1; else
                                              H_norm x (1 - top-2 gap)
    compute_decision_impact(action)         — no hypotheses -> 0.5;
                                              target mentions leading
                                              hypothesis -> gap x 2; else 0.3
    compute_falsification_value(action)     — FIND_COUNTEREVIDENCE -> 1.0
    compute_thread_debt_bonus(action)       — word-overlap match against
                                              high-debt threads, capped 0.3
    compute_priority_with_factors(action)   — (priority, 10-key factors)
                                              clamped to [0, 1]

Monkeypatching notes (verified against the module):
    * ``ENABLE_NEXT_BEST_EVIDENCE`` is a module-level constant read at
      call time by every scorer; tests set it True to exercise the real
      formulas (rule 13 gating is itself covered by a test below).
    * get_case / list_hypotheses_for_case / get_current_version /
      get_pool are imported at module top -> patched on the
      next_best_action module.
    * compute_thread_debt_bonus imports InvestigationThreadRegistry
      lazily from case_engine.investigation_thread -> the fake registry
      is patched onto that module.
"""
from datetime import datetime, timedelta, timezone

import pytest

from case_engine import next_best_action as nba
from case_engine import investigation_thread as thread_mod
from case_engine.next_best_action import (
    compute_eig,
    compute_decision_impact,
    compute_falsification_value,
    compute_thread_debt_bonus,
    compute_priority_with_factors,
    compute_priority,
)
from case_engine.schemas import Action, Case, Hypothesis, HypothesisVersion, InvestigationThread

_NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, rows=None):
        self._rows = rows or []

    async def fetch(self, _sql, *_args):
        return self._rows

    async def fetchrow(self, _sql, *_args):
        return None

    async def fetchval(self, _sql, *_args):
        return 0

    async def execute(self, _sql, *_args):
        return "OK"


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_exc):
        return False


class _FakePool:
    def __init__(self, rows=None):
        self._conn = _FakeConn(rows)

    def acquire(self):
        return _FakeAcquire(self._conn)


class _FakeRegistry:
    """Stand-in for InvestigationThreadRegistry with fixed threads/debt.

    Constructor mirrors the real registry's ``(case_id)`` first argument
    so it can be patched onto the module attribute verbatim.
    """

    def __init__(self, case_id, threads=(), debt=15.0):
        self.case_id = case_id
        self._threads = list(threads)
        self._debt = debt

    async def _ensure_loaded(self):
        return None

    async def get_all_active(self):
        return self._threads

    def compute_debt(self, _thread):
        return self._debt


def _case(required_confidence: float = 0.85) -> Case:
    return Case(
        case_id="case_test",
        title="Test",
        objective="Test objective",
        required_confidence=required_confidence,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _hypothesis(hid: str, statement: str) -> Hypothesis:
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


def _action(
    action_type: str = "RETRIEVE",
    target: str = "fetch deployment logs",
    cost_estimate: float = 0.5,
    risk: float = 0.0,
) -> Action:
    return Action(
        action_id="act_test",
        case_id="case_test",
        action_type=action_type,
        target=target,
        cost_estimate=cost_estimate,
        risk=risk,
        created_at=_NOW,
    )


def _thread(question: str) -> InvestigationThread:
    return InvestigationThread(
        thread_id="thread_test",
        case_id="case_test",
        question=question,
        origin="cycle",
        created_at=_NOW - timedelta(days=30),
        last_progress_at=_NOW - timedelta(days=30),
        strength=1.0,
        decision_impact=1.0,
        uncertainty=1.0,
    )


@pytest.fixture
def nba_enabled(monkeypatch):
    """Enable the real formulas + neuter every DB dependency."""
    monkeypatch.setattr(nba, "ENABLE_NEXT_BEST_EVIDENCE", True)
    monkeypatch.setattr(nba, "get_case", _async_of(_case()))
    monkeypatch.setattr(nba, "list_hypotheses_for_case", _async_of([]))
    monkeypatch.setattr(nba, "get_current_version", _async_of(_version(0.5)))
    monkeypatch.setattr(nba, "get_pool", _async_of(_FakePool()))
    monkeypatch.setattr(
        thread_mod, "InvestigationThreadRegistry", _FakeRegistry
    )


def _async_of(value):
    async def _fn(*_args, **_kwargs):
        return value

    return _fn


# ---------------------------------------------------------------------------
# compute_eig
# ---------------------------------------------------------------------------

class TestComputeEig:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_eig_single_hypothesis_returns_0_1(self, nba_enabled, monkeypatch):
        monkeypatch.setattr(
            nba, "list_hypotheses_for_case", _async_of([_hypothesis("h1", "H one")])
        )
        assert await compute_eig(_action()) == 0.1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_eig_two_hypotheses_uniform(self, nba_enabled, monkeypatch):
        hypotheses = [_hypothesis("h1", "H one"), _hypothesis("h2", "H two")]

        versions = {"h1": _version(0.5), "h2": _version(0.5)}

        async def fake_version(hid):
            return versions[hid]

        monkeypatch.setattr(nba, "list_hypotheses_for_case", _async_of(hypotheses))
        monkeypatch.setattr(nba, "get_current_version", fake_version)
        # uniform pair: H_norm = 1, top-2 gap = 0 -> discrimination 1
        assert await compute_eig(_action()) == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_eig_skewed_pair_below_uniform(self, nba_enabled, monkeypatch):
        hypotheses = [_hypothesis("h1", "H one"), _hypothesis("h2", "H two")]
        versions = {"h1": _version(0.9), "h2": _version(0.1)}

        async def fake_version(hid):
            return versions[hid]

        monkeypatch.setattr(nba, "list_hypotheses_for_case", _async_of(hypotheses))
        monkeypatch.setattr(nba, "get_current_version", fake_version)
        skewed = await compute_eig(_action())
        assert 0.0 < skewed < 1.0
        # H_norm(0.9/0.1) ~= 0.469, gap = 0.8 -> discrimination 0.2 -> ~0.094
        assert skewed == pytest.approx(0.0938, abs=1e-2)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_eig_disabled_flag_returns_stub_0(self, monkeypatch):
        monkeypatch.setattr(nba, "ENABLE_NEXT_BEST_EVIDENCE", False)
        assert await compute_eig(_action()) == 0.0


# ---------------------------------------------------------------------------
# compute_decision_impact / compute_falsification_value
# ---------------------------------------------------------------------------

class TestDecisionImpactAndFalsification:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_decision_impact_no_hypotheses_returns_0_5(self, nba_enabled):
        assert await compute_decision_impact(_action()) == 0.5

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_decision_impact_target_mentions_leading(self, nba_enabled, monkeypatch):
        leading = _hypothesis("h1", "connection pool exhaustion")
        monkeypatch.setattr(nba, "list_hypotheses_for_case", _async_of([leading]))
        monkeypatch.setattr(nba, "get_current_version", _async_of(_version(0.5)))
        # required 0.85 - leading 0.5 -> gap 0.35 -> gap * 2 = 0.7
        action = _action(target="investigate connection pool exhaustion causes")
        assert await compute_decision_impact(action) == pytest.approx(0.7)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_decision_impact_unrelated_target_returns_0_3(self, nba_enabled, monkeypatch):
        leading = _hypothesis("h1", "connection pool exhaustion")
        monkeypatch.setattr(nba, "list_hypotheses_for_case", _async_of([leading]))
        monkeypatch.setattr(nba, "get_current_version", _async_of(_version(0.5)))
        assert await compute_decision_impact(_action(target="disk usage")) == 0.3

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_falsification_value_find_counterevidence(self, nba_enabled):
        assert await compute_falsification_value(_action(action_type="FIND_COUNTEREVIDENCE")) == 1.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_falsification_value_other(self, nba_enabled):
        assert await compute_falsification_value(_action(action_type="SEARCH")) == 0.0
        assert await compute_falsification_value(_action(action_type="RETRIEVE")) == 0.0


# ---------------------------------------------------------------------------
# thread debt bonus (Part 2 §8)
# ---------------------------------------------------------------------------

class TestThreadDebtBonus:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_debt_bonus_word_overlap_with_high_debt_thread(self, nba_enabled, monkeypatch):
        monkeypatch.setattr(
            thread_mod,
            "InvestigationThreadRegistry",
            lambda case_id: _FakeRegistry(case_id, [_thread("Why did latency regress?")], debt=15.0),
        )
        # shares the word "latency" with the thread question
        bonus = await compute_thread_debt_bonus(_action(target="measure latency p99"))
        assert bonus > 0.0
        assert bonus == pytest.approx(0.3)  # debt 15 / 5 * 0.3 capped at 0.3

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_debt_bonus_no_word_overlap_is_zero(self, nba_enabled, monkeypatch):
        monkeypatch.setattr(
            thread_mod,
            "InvestigationThreadRegistry",
            lambda case_id: _FakeRegistry(case_id, [_thread("Why did latency regress?")], debt=15.0),
        )
        assert await compute_thread_debt_bonus(_action(target="inspect disk usage")) == 0.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_debt_bonus_no_active_threads_is_zero(self, nba_enabled, monkeypatch):
        monkeypatch.setattr(
            thread_mod,
            "InvestigationThreadRegistry",
            lambda case_id: _FakeRegistry(case_id, [], debt=15.0),
        )
        assert await compute_thread_debt_bonus(_action(target="latency")) == 0.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_debt_bonus_low_debt_thread_is_small(self, nba_enabled, monkeypatch):
        monkeypatch.setattr(
            thread_mod,
            "InvestigationThreadRegistry",
            lambda case_id: _FakeRegistry(case_id, [_thread("latency regression")], debt=1.0),
        )
        bonus = await compute_thread_debt_bonus(_action(target="check latency"))
        assert 0.0 < bonus < 0.3
        assert bonus == pytest.approx(1.0 / 5.0 * 0.3)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_debt_bonus_no_target_is_zero(self, nba_enabled):
        assert await compute_thread_debt_bonus(_action(target=None)) == 0.0


# ---------------------------------------------------------------------------
# compute_priority_with_factors (the composite)
# ---------------------------------------------------------------------------

class TestComputePriorityWithFactors:
    # Hari ICE improvements pass: the factor breakdown additionally carries
    # "eig_detail" (the action-conditioned EIG's own breakdown: priors,
    # outcome source, outcome count — silent scoring is forbidden).
    _FACTOR_KEYS = {
        "eig", "eig_detail", "impact", "falsification", "cost", "diversity",
        "debt_bonus", "redundancy", "risk", "numerator", "denominator",
    }

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_priority_with_factors_shape(self, nba_enabled):
        priority, factors = await compute_priority_with_factors(_action())
        assert isinstance(priority, float)
        assert 0.0 <= priority <= 1.0
        assert set(factors.keys()) == self._FACTOR_KEYS

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_priority_formula_values(self, nba_enabled):
        # single hypothesis: eig 0.1, impact 0.5 (no hypotheses), falsif 0,
        # cost 0.5 -> (0.1 * 0.5 * 1) / 1.5
        priority, factors = await compute_priority_with_factors(_action(cost_estimate=0.5))
        assert factors["eig"] == 0.1
        assert factors["impact"] == 0.5
        assert factors["falsification"] == 0.0
        assert factors["cost"] == 0.5
        assert factors["debt_bonus"] == 0.0
        assert factors["numerator"] == pytest.approx(0.05)
        assert factors["denominator"] == pytest.approx(1.5)
        assert priority == pytest.approx(0.05 / 1.5)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_priority_clamped_to_1(self, nba_enabled, monkeypatch):
        # Hari ICE improvements pass: the numerator now uses the
        # action-conditioned EIG. Two uniform hypotheses + a
        # FIND_COUNTEREVIDENCE action targeting the leader -> the default
        # outcome model is asymmetric (0.7/0.25 vs 0.25/0.7) -> EIG is high
        # (but < the old entropy-max 1.0); impact 1.0; falsification
        # doubles the numerator; zero cost -> raw priority exceeds 1.0 ->
        # clamped to 1.0. The exact EIG comes from the pure function so the
        # test stays in sync with the model.
        monkeypatch.setattr(nba, "get_case", _async_of(_case(required_confidence=1.0)))
        hypotheses = [_hypothesis("h1", "H one"), _hypothesis("h2", "H two")]
        versions = {"h1": _version(0.5), "h2": _version(0.5)}

        async def fake_version(hid):
            return versions[hid]

        monkeypatch.setattr(nba, "list_hypotheses_for_case", _async_of(hypotheses))
        monkeypatch.setattr(nba, "get_current_version", fake_version)
        # Attach a near-diagnostic outcome model so raw priority exceeds
        # 1.0 (the ATTACHED path of expected_information_gain — richest
        # outcome source, per the planner).
        action = _action(
            action_type="FIND_COUNTEREVIDENCE",
            target="H one",
            cost_estimate=0.0,
        )
        action = action.model_copy(update={
            "result_payload": {
                "predicted_outcomes": [
                    {"likelihoods": {"h1": 0.98, "h2": 0.02}},
                    {"likelihoods": {"h1": 0.02, "h2": 0.98}},
                ]
            }
        })
        priority, factors = await compute_priority_with_factors(action)
        from case_engine.next_best_action import expected_information_gain_pure
        expected_eig = expected_information_gain_pure(
            {"h1": 0.5, "h2": 0.5},
            [{"h1": 0.98, "h2": 0.02}, {"h1": 0.02, "h2": 0.98}],
        )
        assert factors["eig_detail"]["outcome_source"] == "attached"
        assert factors["eig"] == pytest.approx(expected_eig)
        assert factors["eig"] > 0.5  # near-diagnostic test
        assert factors["numerator"] == pytest.approx(expected_eig * 1.0 * 2.0)
        assert priority == 1.0  # raw exceeds 1 -> clamped

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_undiscriminating_action_scores_lower_eig(self, nba_enabled):
        """The audit's Test A vs Test B: two actions, same hypothesis set —
        the one whose outcomes are IDENTICAL under every hypothesis must
        score ~0 EIG, the discriminating one high. This is the property the
        old entropy heuristic could NOT express."""
        from case_engine.next_best_action import expected_information_gain_pure

        priors = {"h1": 0.5, "h2": 0.5}
        uninformative = expected_information_gain_pure(
            priors,
            [{"h1": 0.5, "h2": 0.5}, {"h1": 0.5, "h2": 0.5}],
        )
        discriminating = expected_information_gain_pure(
            priors,
            [{"h1": 0.9, "h2": 0.1}, {"h1": 0.1, "h2": 0.9}],
        )
        assert uninformative == pytest.approx(0.0)
        # 0.53 is the theoretical max for 0.9/0.1 likelihoods with 2
        # hypotheses (residual posterior entropy is irreducible) — the
        # property under test is the CONTRAST, not the absolute value.
        assert discriminating == pytest.approx(0.531, abs=0.01)
        assert discriminating > 10 * (uninformative + 1e-6)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_priority_clamped_to_0(self, nba_enabled):
        # eig 0.1 x impact 0.5 / 1.5 - risk 1.0 -> negative -> clamped 0.0
        action = _action(cost_estimate=0.5, risk=1.0)
        priority, factors = await compute_priority_with_factors(action)
        assert factors["risk"] == 1.0
        assert priority == 0.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_priority_debt_bonus_adds_to_score(self, nba_enabled, monkeypatch):
        monkeypatch.setattr(
            thread_mod,
            "InvestigationThreadRegistry",
            lambda case_id: _FakeRegistry(case_id, [_thread("fetch deployment logs")], debt=15.0),
        )
        base_action = _action(target="fetch deployment logs")
        bonus = await compute_thread_debt_bonus(base_action)
        assert bonus == pytest.approx(0.3)
        priority, factors = await compute_priority_with_factors(base_action)
        assert factors["debt_bonus"] == pytest.approx(0.3)

        no_debt_action = _action(target="unrelated target")
        p_no_debt, f_no_debt = await compute_priority_with_factors(no_debt_action)
        assert f_no_debt["debt_bonus"] == 0.0
        assert priority > p_no_debt

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_priority_wrapper_matches_factors(self, nba_enabled):
        action = _action()
        priority, factors = await compute_priority_with_factors(action)
        assert await compute_priority(action) == pytest.approx(priority)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_compute_priority_disabled_flag_all_stubs(self, monkeypatch):
        monkeypatch.setattr(nba, "ENABLE_NEXT_BEST_EVIDENCE", False)
        priority, factors = await compute_priority_with_factors(_action())
        assert priority == 0.0
        assert set(factors.keys()) == self._FACTOR_KEYS
        # every scorer is stubbed to 0.0; only the pass-through action
        # fields (cost, risk) and the derived denominator stay non-zero
        for key in ("eig", "impact", "falsification", "diversity",
                    "debt_bonus", "redundancy", "numerator"):
            assert factors[key] == 0.0, key
        assert factors["cost"] == 0.5
        assert factors["risk"] == 0.0