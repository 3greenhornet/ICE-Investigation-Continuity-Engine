"""benchmark/metrics.py — Benchmark metrics (spec §17.3 + P0 rebuild).

Owner:      ICE revamp Part 4 §17.3; REBUILT by the Hari ICE improvements
            pass (P0: "Rebuild the benchmark ... Then measure:" the list
            below is that list, implemented).
Purpose:    Pure functions over run_all_cases() result dicts:
    * hypothesis_top1_accuracy  — leading hypothesis matches the root cause
    * hypothesis_top3_accuracy  — root cause appears in the top 3
    * hypothesis_diversity      — mean pairwise distinctness of the
                                  generated explanation set
    * contradiction_precision   — PAIR-matched (was count-comparison)
    * contradiction_recall      — PAIR-matched (new)
    * decision_correctness      — ACT only counts when the leading
                                  hypothesis is actually right (was "ACT or
                                  INVESTIGATE == correct")
    * calibration_error         — binned |confidence − accuracy| (ECE)
    * abstention_quality        — abstained cases whose leading hypothesis
                                  was wrong (correct humility)
    * evidence_utilization      — evidence actually used (claims extracted)
    * investigation_efficiency  — confidence gained per executed action
    * action_evidence_yield     — executed actions that produced evidence
    * evidence_trace_coverage / mean_latency — retained
    * report                    — aggregates everything
Legacy proxy metrics (hypothesis_ranking_accuracy, decision_accuracy) are
kept for backward comparison with old runs.
Deps:       none (stdlib only). Failed cases ({"case_dir", "error"}) are
            excluded from numerators and reported in "failed_cases".
"""

_FAILED = object()


def _valid(results: list[dict]) -> list[dict]:
    return [r for r in results if "error" not in r]


def _match_pair(detected: dict, expected: dict) -> bool:
    """Pair match: both claim statements pairwise overlap."""

    _SUFFIXES = ("ations", "ation", "ional", "ions", "ion", "ities",
                 "ity", "ness", "ments", "ment", "ings", "ing", "ed",
                 "es", "s")

    def _stem(token: str) -> str:
        t = token
        for suffix in _SUFFIXES:
            if t.endswith(suffix) and len(t) - len(suffix) >= 4:
                return t[: -len(suffix)]
        return t

    def toks(s: str) -> set[str]:
        import re

        return {
            _stem(t)
            for t in re.findall(r"[a-z0-9]+", (s or "").lower())
            if len(t) > 2
        }

    def one_way(a: str, b: str, threshold: float = 0.35) -> bool:
        ta, tb = toks(a), toks(b)
        if not ta or not tb:
            return False
        return len(ta & tb) / min(len(ta), len(tb)) >= threshold

    da = detected.get("claim_a", "")
    db = detected.get("claim_b", "")
    ea = expected.get("claim_a", "")
    eb = expected.get("claim_b", "")
    direct = (one_way(da, ea) and one_way(db, eb)) or (
        one_way(da, eb) and one_way(db, ea)
    )
    return direct


def hypothesis_top1_accuracy(results: list[dict]) -> float:
    """Fraction of cases whose LEADING hypothesis matches the root cause."""
    valid = _valid(results)
    if not valid:
        return 0.0
    correct = sum(1 for r in valid if r.get("leading_matches_root_cause"))
    return correct / len(valid)


def hypothesis_top3_accuracy(results: list[dict]) -> float:
    """Root cause present among the top-3 ranked hypotheses."""
    valid = _valid(results)
    if not valid:
        return 0.0
    correct = sum(1 for r in valid if r.get("top3_matches_root_cause"))
    return correct / len(valid)


def hypothesis_fault_class_accuracy(results: list[dict]) -> float:
    """Fraction of cases whose leading candidate attended to the right KIND
    of evidence: the runner's per-case `leading_matches_fault_class` flag
    (observed-signals overlap with the root-cause text, runner.py). Coarser
    than statement-level accuracy and reported separately — never summed
    with it (the phrasing gap is the LLM path's job to close).
    """
    valid = _valid(results)
    if not valid:
        return 0.0
    correct = sum(1 for r in valid if r.get("leading_matches_fault_class"))
    return correct / len(valid)


def hypothesis_diversity(results: list[dict]) -> float:
    """Mean hypothesis-set diversity across cases (None-safe)."""
    valid = [
        r for r in _valid(results)
        if r.get("hypothesis_diversity") is not None
    ]
    if not valid:
        return 0.0
    return sum(r["hypothesis_diversity"] for r in valid) / len(valid)


def contradiction_precision(results: list[dict]) -> float:
    """PAIR-matched precision: detected pairs that match expected pairs.

    Replaces the old count-comparison ("detected == 0 -> 1.0 if expected ==
    0" style), which the audit rejected: "it does not match the actual
    contradiction pairs."
    """
    valid = _valid(results)
    if not valid:
        return 0.0
    true_positives = 0
    detected_total = 0
    for r in valid:
        detected = r.get("detected_contradiction_pairs") or []
        expected = r.get("hidden_resolution", {}).get("contradictions", [])
        detected_total += len(detected)
        for d in detected:
            if any(_match_pair(d, e) for e in expected):
                true_positives += 1
    if detected_total == 0:
        return 1.0 if all(
            not (r.get("hidden_resolution", {}).get("contradictions"))
            for r in valid
        ) else 0.0
    return true_positives / detected_total


def contradiction_recall(results: list[dict]) -> float:
    """PAIR-matched recall: expected pairs the detector actually found."""
    valid = _valid(results)
    if not valid:
        return 0.0
    expected_total = 0
    found = 0
    for r in valid:
        detected = r.get("detected_contradiction_pairs") or []
        expected = r.get("hidden_resolution", {}).get("contradictions", [])
        expected_total += len(expected)
        for e in expected:
            if any(_match_pair(d, e) for d in detected):
                found += 1
    if expected_total == 0:
        return 1.0
    return found / expected_total


def decision_correctness(results: list[dict]) -> float:
    """ACT is correct only when the leading hypothesis is actually right.

    The old proxy ("ACT or INVESTIGATE counts as correct") is replaced:
    committing to a course on a WRONG explanation is a failure, not a
    success.
    """
    valid = _valid(results)
    if not valid:
        return 0.0
    correct = sum(1 for r in valid if r.get("decision_correct"))
    return correct / len(valid)


def calibration_error(results: list[dict], bins: int = 5) -> float:
    """Expected Calibration Error over leading-hypothesis confidences.

    Bins the leading confidence; each bin's |mean confidence − accuracy|
    is weighted by bin size. 0.0 = perfectly calibrated. (Reminder: ICE
    confidence is explicitly NOT a probability — this metric measures how
    far it sits from empirical correctness, which is exactly what the
    audit asked to know.)
    """
    valid = [
        r for r in _valid(results)
        if r.get("leading_hypothesis_confidence") is not None
    ]
    if not valid:
        return 0.0
    bucket = [[] for _ in range(bins)]
    for r in valid:
        conf = min(0.999, max(0.0, r["leading_hypothesis_confidence"]))
        idx = int(conf * bins)
        bucket[idx].append(r.get("leading_matches_root_cause", False))
    ece = 0.0
    total = len(valid)
    for entries in bucket:
        if not entries:
            continue
        mean_conf = ((bucket.index(entries) + 0.5) / bins)
        acc = sum(1 for e in entries if e) / len(entries)
        ece += (len(entries) / total) * abs(mean_conf - acc)
    return round(ece, 4)


def abstention_quality(results: list[dict]) -> dict:
    """Correct humility: abstaining when the answer was wrong.

    Returns {abstained, correct_abstentions, wrong_leading_abstained,
    wrong_leading_acted} — the last one (acted on a wrong explanation) is
    the most expensive failure mode.
    """
    valid = _valid(results)
    abstained = [r for r in valid if r.get("decision_type") == "ABSTAIN"]
    wrong = [r for r in valid if not r.get("leading_matches_root_cause")]
    wrong_leading_acted = [
        r for r in wrong if r.get("decision_type") == "ACT"
    ]
    return {
        "abstained": len(abstained),
        "correct_abstentions": sum(
            1 for r in abstained if not r.get("leading_matches_root_cause")
        ),
        "wrong_leading_abstained": sum(
            1 for r in abstained if not r.get("leading_matches_root_cause")
        ),
        "wrong_leading_acted": len(wrong_leading_acted),
    }


def action_evidence_yield(results: list[dict]) -> float:
    """Executed actions that produced useful new evidence / executed total."""
    valid = _valid(results)
    executed_total = 0
    useful = 0
    for r in valid:
        outcomes = r.get("executed_action_outcomes") or []
        executed = [a for a in outcomes if a.get("status") == "completed"]
        executed_total += len(executed)
        useful += r.get("useful_action_evidence", 0)
    if executed_total == 0:
        return 0.0
    return useful / executed_total


def investigation_efficiency(results: list[dict]) -> float:
    """Confidence gained per executed action (audit: 'accuracy gained per
    tool call'); clamped to [0, 1]."""
    valid = _valid(results)
    if not valid:
        return 0.0
    scores = []
    for r in valid:
        executed = len(r.get("executed_action_outcomes") or [])
        gain = max(0.0, r.get("leading_hypothesis_confidence", 0.0) - 0.3)
        scores.append(gain / (1.0 + executed))
    mean = sum(scores) / len(scores)
    return max(0.0, min(1.0, mean))


def evidence_trace_coverage(results: list[dict]) -> float:
    """Average ETC (evidence trace coverage) across non-error cases."""
    valid = [r["etc"] for r in _valid(results) if "etc" in r]
    return sum(valid) / len(valid) if valid else 0.0


def evidence_utilization(results: list[dict]) -> float:
    """Evidence actually used: cases where at least one claim was
    extracted from at least one evidence item."""
    valid = _valid(results)
    scored = [r for r in valid if int(r.get("evidence_count", 0) or 0) >= 1]
    if not scored:
        return 0.0
    hit = sum(1 for r in scored if int(r.get("claim_count", 0) or 0) >= 1)
    return hit / len(scored)


def mean_latency(results: list[dict]) -> float:
    valid = [r["duration_seconds"] for r in _valid(results)]
    return sum(valid) / len(valid) if valid else 0.0


# ---------------------------------------------------------------------------
# Legacy proxies (kept for comparison against pre-rebuild runs)
# ---------------------------------------------------------------------------


def hypothesis_ranking_accuracy(results: list[dict]) -> float:
    """LEGACY proxy: word-set overlap > 0.5 vs the hidden primary."""
    if not results:
        return 0.0
    correct = 0
    for r in results:
        if "error" in r:
            continue
        expected = (r["hidden_resolution"].get("primary_hypothesis") or "").lower()
        actual = (r.get("leading_hypothesis_statement") or "").lower()
        expected_terms = set(expected.split())
        actual_terms = set(actual.split())
        overlap = len(expected_terms & actual_terms) / max(len(expected_terms), 1)
        if overlap > 0.5:
            correct += 1
    return correct / len(results)


def decision_accuracy(results: list[dict]) -> float:
    """LEGACY proxy: ACT or INVESTIGATE counts (the metric the audit
    rejected — kept only to quantify the difference)."""
    if not results:
        return 0.0
    correct = 0
    for r in results:
        if "error" in r:
            continue
        if r.get("decision_type") in ("ACT", "INVESTIGATE"):
            correct += 1
    return correct / len(results)


def report(results: list[dict]) -> dict:
    """Aggregate the rebuilt metric set + legacy proxies + failures."""
    valid = _valid(results)
    return {
        "cases": len(results),
        "cases_evaluated": len(valid),
        "failed_cases": [
            {"case_dir": r.get("case_dir"), "error": r.get("error")}
            for r in results if "error" in r
        ],
        # --- P0 rebuilt metrics ---
        "hypothesis_top1_accuracy": hypothesis_top1_accuracy(results),
        "hypothesis_fault_class_accuracy": hypothesis_fault_class_accuracy(results),
        "hypothesis_top3_accuracy": hypothesis_top3_accuracy(results),
        "hypothesis_diversity": hypothesis_diversity(results),
        "contradiction_precision": contradiction_precision(results),
        "contradiction_recall": contradiction_recall(results),
        "decision_correctness": decision_correctness(results),
        "calibration_error": calibration_error(results),
        "abstention_quality": abstention_quality(results),
        "action_evidence_yield": action_evidence_yield(results),
        "investigation_efficiency": investigation_efficiency(results),
        "evidence_utilization": evidence_utilization(results),
        "evidence_trace_coverage": evidence_trace_coverage(results),
        "mean_latency_seconds": mean_latency(results),
        # --- legacy proxies (pre-rebuild comparison) ---
        "legacy_hypothesis_ranking_accuracy": hypothesis_ranking_accuracy(results),
        "legacy_decision_accuracy": decision_accuracy(results),
        "per_case": results,
    }


# Canonical display/serialization order for the rebuilt metric set. The
# offline runner's report printer iterates THIS list (with .get(k, "n/a")),
# so the printer can never again require a key that report() does not
# produce, and a missing metric degrades to "n/a" instead of crashing the
# run after the expensive part has already completed.
REPORT_KEY_ORDER = [
    "hypothesis_top1_accuracy",
    "hypothesis_fault_class_accuracy",
    "hypothesis_top3_accuracy",
    "hypothesis_diversity",
    "contradiction_precision",
    "contradiction_recall",
    "decision_correctness",
    "calibration_error",
    "action_evidence_yield",
    "investigation_efficiency",
    "evidence_utilization",
    "evidence_trace_coverage",
    "mean_latency_seconds",
    "legacy_hypothesis_ranking_accuracy",
    "legacy_decision_accuracy",
]
