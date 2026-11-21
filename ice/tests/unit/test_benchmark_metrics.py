"""Unit tests for benchmark/metrics.py — the rebuilt metric set.

These are fast, no-DB, no-LLM tests of the pure aggregation functions.
They exist because of a real failure: the offline runner's report printer
once required `hypothesis_fault_class_accuracy` while report() did not
produce it (interrupted wiring), crashing the benchmark AFTER the expensive
part had already run. The drift guard below makes that class of bug a
test failure instead of a runtime crash.
"""
import io
import sys
from contextlib import redirect_stdout

import pytest

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from benchmark.metrics import (  # noqa: E402
    REPORT_KEY_ORDER,
    hypothesis_fault_class_accuracy,
    report,
)

pytestmark = pytest.mark.unit


def _case(**over):
    row = {
        "case_id": "c1",
        "case_dir": "x",
        "induction_path": "deterministic",
        "leading_hypothesis_statement": "s",
        "leading_hypothesis_confidence": 0.6,
        "leading_matches_root_cause": True,
        "leading_matches_fault_class": True,
        "top3_matches_root_cause": True,
        "hypothesis_diversity": 0.5,
        "detected_contradiction_pairs": [],
        "hidden_resolution": {"primary_hypothesis": "s", "contradictions": []},
        "decision_type": "INVESTIGATE",
        "decision_confidence": 0.6,
        "decision_correct": True,
        "executed_action_outcomes": [],
        "useful_action_evidence": 0,
        "etc": 1.0,
        "claim_count": 3,
        "evidence_count": 5,
        "action_count": 1,
        "duration_seconds": 0.5,
    }
    row.update(over)
    return row


class TestFaultClassAccuracy:
    def test_fraction_of_true_flags(self):
        rows = [_case(), _case(), _case(leading_matches_fault_class=False)]
        assert hypothesis_fault_class_accuracy(rows) == pytest.approx(2 / 3)

    def test_empty_and_error_rows(self):
        assert hypothesis_fault_class_accuracy([]) == 0.0
        # error rows are not valid results and must not count in the denom
        rows = [_case(), {"case_dir": "y", "error": "boom"}]
        assert hypothesis_fault_class_accuracy(rows) == 1.0

    def test_missing_flag_counts_as_false_not_crash(self):
        rows = [_case(), _case(), {"case_dir": "z"}]
        assert hypothesis_fault_class_accuracy(rows) == pytest.approx(2 / 3)


class TestReportDriftGuard:
    """report() must produce every key the canonical display order lists —
    this is the guard that would have caught the interrupted wiring."""

    def test_report_contains_all_report_keys(self):
        metrics = report([_case()])
        for k in REPORT_KEY_ORDER:
            assert k in metrics, f"report() is missing {k!r} (printer crash risk)"

    def test_report_contains_abstention_quality(self):
        assert "abstention_quality" in report([_case()])

    def test_report_fault_class_value(self):
        metrics = report([_case(), _case(leading_matches_fault_class=False)])
        assert metrics["hypothesis_fault_class_accuracy"] == pytest.approx(0.5)


class _PrinterShim:
    pass


class TestPrinterTolerance:
    """The offline runner's _print_report must degrade to n/a, never raise,
    when a metric is absent (e.g. legacy results files from older runs)."""

    def test_print_report_tolerates_missing_keys(self):
        from scripts.run_benchmark_offline import _print_report

        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_report(
                {"cases": 5, "cases_evaluated": 5, "failed_cases": [],
                 "hypothesis_top1_accuracy": 0.2},
                label="PARTIAL",
            )
        out = buf.getvalue()
        assert "PARTIAL" in out
        assert "hypothesis_top1_accuracy" in out and "0.2" in out
        assert "n/a" in out  # missing keys degrade, not crash
