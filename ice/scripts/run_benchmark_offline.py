#!/usr/bin/env python3
"""scripts/run_benchmark_offline.py — run the REBUILT benchmark end-to-end.

The Hari ICE improvements pass, P0 acceptance: "the benchmark was not run"
is no longer true. This script:
    1. connects via DATABASE_URL (pgvector Postgres),
    2. applies the ICE migrations (idempotent for 018-020),
    3. runs the no-leakage benchmark over benchmark/cases (the engine sees
       ONLY case.json + evidence; hypotheses come from the induction
       engine; the hidden resolution is opened by the evaluator alone),
    4. optionally also runs the LEGACY seeded mode for comparison,
    5. prints the rebuilt metric report and writes benchmark/results/.

Usage:
    python scripts/run_benchmark_offline.py [--legacy] [--execute-actions]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import init_db, close_db  # noqa: E402

BENCHMARK_ROOT = Path(__file__).resolve().parent.parent / "benchmark" / "cases"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "benchmark" / "results"

CONFTEST_MIGRATIONS = [
    "db/migrations/012a_safety_check.sql",
    "db/migrations/012_case_model.sql",
    "db/migrations/013_evidence_graph.sql",
    "db/migrations/014_case_events.sql",
    "db/migrations/015_external_analysis_and_case_type.sql",
    "db/migrations/016_contradiction_uniqueness.sql",
    "db/migrations/017_proposed_edges.sql",
]
IDEMPOTENT_MIGRATIONS = [
    "db/migrations/018_reasoning_loop.sql",
    "db/migrations/019_plan_and_workspace.sql",
    "db/migrations/020_llm_ledger_and_mechanism_lab.sql",
    "db/migrations/021_project_goals.sql",
    "db/migrations/022_goal_attributes.sql",
    "db/migrations/023_lab_propose_action.sql",
]


async def apply_migrations() -> None:
    from db.connection import get_pool

    pool = await get_pool()
    root = Path(__file__).resolve().parent.parent
    async with pool.acquire() as conn:
        already = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='cases')"
        )
        if not already:
            for m in CONFTEST_MIGRATIONS:
                with open(root / m) as fh:
                    await conn.execute(fh.read())
        for m in IDEMPOTENT_MIGRATIONS:
            with open(root / m) as fh:
                await conn.execute(fh.read())


async def main(run_legacy: bool, execute_actions: bool) -> int:
    await init_db()
    from db.connection import get_pool

    if await get_pool() is None:
        print("ERROR: DATABASE_URL not set / DB unreachable")
        return 1

    await apply_migrations()

    # Seed the mechanism lab catalogue while we are here (idempotent).
    try:
        from case_engine.mechanism_lab import seed_mechanism_lab

        n = await seed_mechanism_lab()
        if n:
            print(f"mechanism_lab seeded with {n} donor mechanisms")
    except Exception as exc:
        print(f"(mechanism_lab seed skipped: {exc})")

    from benchmark.runner import run_all_cases
    from benchmark.metrics import report

    print(f"\n=== ICE benchmark (rebuilt, no leakage) — {BENCHMARK_ROOT} ===")
    results = await run_all_cases(
        str(BENCHMARK_ROOT),
        seed_hypotheses_from_resolution=False,
        execute_actions=execute_actions,
    )
    metrics = report(results)
    _print_report(metrics, label="HONEST (induction-driven)")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"offline_{stamp}.json"
    out.write_text(json.dumps(metrics, indent=2, default=str))
    print(f"\nresults written to {out}")

    if run_legacy:
        print("\n=== LEGACY mode (hypotheses seeded from resolution) ===")
        legacy_results = await run_all_cases(
            str(BENCHMARK_ROOT),
            seed_hypotheses_from_resolution=True,
            execute_actions=execute_actions,
        )
        legacy_metrics = report(legacy_results)
        _print_report(legacy_metrics, label="LEGACY (seeded)")
        legacy_out = RESULTS_DIR / f"legacy_{stamp}.json"
        legacy_out.write_text(json.dumps(legacy_metrics, indent=2, default=str))
        print(f"\nresults written to {legacy_out}")

    await close_db()
    return 0


def _print_report(metrics: dict, label: str) -> None:
    print(f"\n--- {label} ---")
    print(f"cases: {metrics.get('cases')}  evaluated: {metrics.get('cases_evaluated')}")
    if metrics.get("failed_cases"):
        for fc in metrics["failed_cases"]:
            print(f"  FAILED {fc['case_dir']}: {fc['error'][:120]}")
    # Single source of truth: the canonical order lives in benchmark.metrics
    # (REPORT_KEY_ORDER); a key that report() did not produce prints as n/a
    # instead of crashing the run after the expensive part already finished.
    from benchmark.metrics import REPORT_KEY_ORDER

    for k in REPORT_KEY_ORDER:
        print(f"  {k:38s} {metrics.get(k, 'n/a')}")
    print(f"  abstention_quality: {metrics.get('abstention_quality', 'n/a')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", action="store_true",
                        help="also run the legacy seeded mode for comparison")
    parser.add_argument("--execute-actions", action="store_true",
                        help="approve+execute proposed actions during the run")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.legacy, args.execute_actions)))
