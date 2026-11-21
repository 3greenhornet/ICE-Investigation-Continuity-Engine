"""End-to-end evaluation. Requires LLM + DB. Verifies the full pipeline
runs on benchmark cases and produces meaningful output.

This is a SMOKE test, not a full evaluation. The full benchmark (5 cases x
6 baselines x 8 metrics) is scripts/run_full_benchmark.py, which is slower
and produces the numbers you put on a resume. This test runs ONE case
against the full engine and checks that the pipeline produced sensible
intermediate state.

This file is the doc §5.5 complete example 3, kept verbatim with one
documented addition: besides the fixture-dir and DB skips, each test also
skips when no LLM provider key is configured — run_engine_on_case calls
the real extraction LLM, and without a key the run fails rather than
degrades (the CI eval job is the environment that provides keys).
"""
import os

import pytest

from benchmark.runner import run_engine_on_case
from case_engine.hypothesis import list_hypotheses_for_case
from case_engine.contradiction import get_active_contradictions


def _require_llm_key():
    """Skip when no provider key is set (eval needs a real LLM)."""
    keys = ("GROQ_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY")
    if not any(os.getenv(k, "").strip() for k in keys):
        pytest.skip("no LLM provider key set (GROQ/OPENROUTER/GEMINI/MISTRAL)")


@pytest.mark.eval
@pytest.mark.asyncio
async def test_case_01_pipeline_produces_expected_state(db_pool):
    """Run case_01_latency through the full engine. Check pipeline output."""
    _require_llm_key()
    case_dir = "benchmark/cases/case_01_latency"
    if not os.path.exists(case_dir):
        pytest.skip(f"Case fixture missing: {case_dir}")

    result = await run_engine_on_case(case_dir)

    # Basic sanity
    assert result["case_id"].startswith("case_")
    assert result["hypothesis_count"] >= 2
    assert result["claim_count"] >= 1
    assert result["duration_seconds"] > 0
    assert 0.0 <= result["etc"] <= 1.0
    assert result["hidden_resolution"]["primary_hypothesis"]

    # Pipeline produced hypotheses
    hypotheses = await list_hypotheses_for_case(result["case_id"])
    assert len(hypotheses) >= 2

    # Verify the leading hypothesis is a real one
    assert result["leading_hypothesis_statement"]
    assert result["leading_hypothesis_confidence"] > 0


@pytest.mark.eval
@pytest.mark.asyncio
async def test_case_02_memory_leak_runs(db_pool):
    """Run case_02_memory_leak through the full engine. Check pipeline."""
    _require_llm_key()
    case_dir = "benchmark/cases/case_02_memory_leak"
    if not os.path.exists(case_dir):
        pytest.skip(f"Case fixture missing: {case_dir}")
    result = await run_engine_on_case(case_dir)
    assert result["case_id"].startswith("case_")
    assert result["decision_type"] in ("ACT", "INVESTIGATE", "ABSTAIN")


@pytest.mark.eval
@pytest.mark.asyncio
async def test_case_03_auth_failure_runs(db_pool):
    """Run case_03_auth_failure through the full engine."""
    _require_llm_key()
    case_dir = "benchmark/cases/case_03_auth_failure"
    if not os.path.exists(case_dir):
        pytest.skip(f"Case fixture missing: {case_dir}")
    result = await run_engine_on_case(case_dir)
    assert result["case_id"].startswith("case_")


@pytest.mark.eval
@pytest.mark.asyncio
async def test_case_04_data_inconsistency_runs(db_pool):
    """Run case_04_data_inconsistency through the full engine."""
    _require_llm_key()
    case_dir = "benchmark/cases/case_04_data_inconsistency"
    if not os.path.exists(case_dir):
        pytest.skip(f"Case fixture missing: {case_dir}")
    result = await run_engine_on_case(case_dir)
    assert result["case_id"].startswith("case_")


@pytest.mark.eval
@pytest.mark.asyncio
async def test_case_05_deploy_loop_runs(db_pool):
    """Run case_05_deploy_loop through the full engine."""
    _require_llm_key()
    case_dir = "benchmark/cases/case_05_deploy_loop"
    if not os.path.exists(case_dir):
        pytest.skip(f"Case fixture missing: {case_dir}")
    result = await run_engine_on_case(case_dir)
    assert result["case_id"].startswith("case_")