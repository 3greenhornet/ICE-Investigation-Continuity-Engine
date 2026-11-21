"""benchmark/ablation.py — Ablation runner (reviewer audit gap #15).

Owner:      ICE revamp, audit gap #15 (the PDF mandates a formal ablation
            table — "Remove attention / persistence / ..." — but spec §17
            only ships baselines B1–B6).
Purpose:    run_ablation() executes run_engine_on_case with a set of
            feature flags forced on/off via os.environ (prior values saved
            and restored in try/finally); run_ablation_matrix() runs the
            full-featured engine plus the three meaningful ablations
            (4 configurations total).
Deps:       benchmark.runner.run_engine_on_case, benchmark.baselines
            (set_env_flags / restore_env_flags — one shared, safe toggle
            implementation).

Advisory-flag caveat (same class as baselines B4/B5): flags that
case_engine modules read at IMPORT time — ENABLE_NEXT_BEST_EVIDENCE and
ENABLE_FALSIFICATION_LOOP (bound to module constants in
case_engine.next_best_action / case_engine.action) — are advisory when
flipped here, because the constants were already bound at import. The
ablation still records the requested config so the harness output stays
truthful. For full effect, set those flags BEFORE importing case_engine
(the pattern scripts/verify_part3.py uses). ENABLE_CONTRADICTIONS and
ENABLE_HYPOTHESIS_VERSIONING are not read by the engine at all today
(documented in benchmark/baselines.py); they are carried for the day they
are wired.

Each run_ablation call re-runs the case from scratch (run_engine_on_case
always creates a fresh case), so the configurations are independent
samples rather than mutations of one shared case.
"""

from benchmark.baselines import restore_env_flags, set_env_flags
from benchmark.runner import run_engine_on_case


async def run_ablation(case_dir: str, ablations: dict[str, bool]) -> dict:
    """Run the engine on one case with env flags forced per ``ablations``.

    Preconditions:
        case_dir is a benchmark case directory (case.json +
        hidden_resolution.json present).
        Every key of ``ablations`` is an env flag name; values are bools.
    Postconditions:
        Returns a dict with exactly the keys:
            ablation_config              — dict copy of ``ablations``
            leading_hypothesis_statement — str
            leading_hypothesis_confidence — float
            decision_type                — str
            contradiction_count          — int
            duration_seconds             — float (real timing, runner #37)
        The environment is restored to its prior state even when the run
        raises.
    Raises:
        Whatever run_engine_on_case raises (after env restoration).
    Side effects:
        One full engine run (case, evidence, claims, hypotheses,
        contradictions, actions, decision, events).
    """
    saved = set_env_flags(ablations)
    try:
        result = await run_engine_on_case(case_dir)
    finally:
        restore_env_flags(saved)

    return {
        "ablation_config": dict(ablations),
        "leading_hypothesis_statement": result.get(
            "leading_hypothesis_statement", ""
        ),
        "leading_hypothesis_confidence": result.get(
            "leading_hypothesis_confidence", 0.0
        ),
        "decision_type": result.get("decision_type", ""),
        "contradiction_count": result.get("contradiction_count", 0),
        "duration_seconds": result.get("duration_seconds", 0.0),
    }


async def run_ablation_matrix(case_dir: str) -> list[dict]:
    """Run the full-featured engine plus three ablations (4 configs).

    Configurations (audit gap #15's ablation table, ICE feature scope):
        full_engine       — no flags flipped (the reference run)
        no_contradictions — ENABLE_CONTRADICTIONS=false
        no_versioning     — ENABLE_HYPOTHESIS_VERSIONING=false
        no_next_best      — ENABLE_NEXT_BEST_EVIDENCE=false
    A "no_falsification" config is omitted because ENABLE_FALSIFICATION_LOOP
    defaults to false — it would be identical to full_engine.

    Postconditions:
        Returns a list of 4 rows. Each row is a run_ablation result dict
        (6 keys) plus "ablation_name" identifying the configuration, in the
        order listed above.
    Raises:
        Whatever run_ablation raises for a configuration (the sweep does
        not swallow errors; call sites that need fault isolation should
        wrap each call).
    Side effects:
        4 full engine runs on the same case directory (independent cases).
    """
    configs: list[tuple[str, dict[str, bool]]] = [
        ("full_engine", {}),
        ("no_contradictions", {"ENABLE_CONTRADICTIONS": False}),
        ("no_versioning", {"ENABLE_HYPOTHESIS_VERSIONING": False}),
        ("no_next_best", {"ENABLE_NEXT_BEST_EVIDENCE": False}),
    ]
    results: list[dict] = []
    for name, flags in configs:
        row = await run_ablation(case_dir, flags)
        row["ablation_name"] = name
        results.append(row)
    return results