"""scripts/verify_part2.py — End-to-end verification of Part 2 modules.

Owner:      ICE revamp, spec Part 2 §6.
Usage:      python scripts/verify_part2.py
            (requires Postgres + pgvector per docker-compose.yml and LLM
            keys via .env — see .env.example; there is no DB in the sandbox
            build environment, so this script is validated there by
            py_compile and run in the Docker environment.)

Exercises, in order (8 steps):
    1. create_case                        (case_ id, status "open")
    2. ingest_evidence                    (HUMAN source via _create_source;
                                           auto-classified MEASUREMENT)
    3. duplicate detection                (same content twice -> same
                                           duplicate_cluster_id)
    4. extract_claims                     (>= 1 claim, MEASUREMENT present)
    5. create_hypothesis                  (v1: version 1, prior None)
    6. update_hypothesis                  (v2: version 2, prior 0.5,
                                           history length 2, v1 unchanged)
    7. mark_superseded                    (status SUPERSEDED +
                                           superseded_by set)
    8. set_hypothesis_status + get_case_summary
                                          (leading hypothesis matches,
                                           evidence_total >= 3, claims >= 1,
                                           hypotheses == 1, events >= 6)

Acceptance: the script exits with status 0 and prints
"Part 2 verification PASSED".
"""

import asyncio

from dotenv import load_dotenv

load_dotenv()

from case_engine.case import create_case, get_case_summary
from case_engine.evidence import (
    _create_source,
    ingest_evidence,
    mark_superseded,
    get_evidence,
)
from case_engine.claim import extract_claims
from case_engine.hypothesis import (
    create_hypothesis,
    update_hypothesis,
    get_current_version,
    get_version_history,
    set_hypothesis_status,
)


async def main() -> None:
    # 1. Case -----------------------------------------------------------
    case = await create_case("Test incident", "Find root cause of p95 spike")
    assert case.case_id.startswith("case_")
    assert case.status == "open"
    print(f"[1/8] Case created: {case.case_id}")

    # 2. Evidence (HUMAN source) ----------------------------------------
    source = await _create_source(case.case_id, "HUMAN", None)
    ev = await ingest_evidence(
        case_id=case.case_id,
        content="After rollback, p95 latency returned to baseline (240ms).",
        source_id=source.source_id,
    )
    assert ev.evidence_type == "MEASUREMENT"
    print(f"[2/8] Evidence ingested: {ev.evidence_id} ({ev.evidence_type})")

    # 3. Duplicate detection --------------------------------------------
    ev_dup = await ingest_evidence(
        case_id=case.case_id,
        content="After rollback, p95 latency returned to baseline (240ms).",
        source_id=source.source_id,
    )
    assert ev_dup.duplicate_cluster_id == ev.duplicate_cluster_id
    print(f"[3/8] Duplicate detected: cluster={ev_dup.duplicate_cluster_id}")

    # 4. Claim extraction -------------------------------------------------
    claims = await extract_claims(ev.evidence_id)
    assert len(claims) >= 1
    assert any(c.claim_type == "MEASUREMENT" for c in claims)
    print(f"[4/8] Claims extracted: {len(claims)}")

    # 5. Hypothesis creation ---------------------------------------------
    h = await create_hypothesis(
        case_id=case.case_id,
        statement="Connection pool regression caused latency",
        initial_confidence=0.5,
    )
    v1 = await get_current_version(h.hypothesis_id)
    assert v1.version_number == 1
    assert v1.prior_confidence is None
    print(f"[5/8] Hypothesis v1: {h.hypothesis_id} @ {v1.confidence}")

    # 6. Hypothesis versioning (append-only) ------------------------------
    v2 = await update_hypothesis(
        hypothesis_id=h.hypothesis_id,
        new_confidence=0.7,
        reason="E supports",
        cause_event_id="evt_test",
        added_supporting_evidence=[ev.evidence_id],
    )
    assert v2.version_number == 2
    assert v2.prior_confidence == 0.5
    history = await get_version_history(h.hypothesis_id)
    assert len(history) == 2
    assert history[1].confidence == 0.5  # v1 unchanged (history is DESC)
    print("[6/8] Hypothesis v2 created; v1 unchanged")

    # 7. Supersede chain ---------------------------------------------------
    ev_stale = await ingest_evidence(
        case_id=case.case_id,
        content="Old measurement: p95 was 500ms before rollback.",
        source_id=source.source_id,
    )
    await mark_superseded(
        ev_stale.evidence_id, ev.evidence_id, "Newer measurement available"
    )
    refreshed = await get_evidence(ev_stale.evidence_id)
    assert refreshed.status == "SUPERSEDED"
    assert refreshed.superseded_by == ev.evidence_id
    print("[7/8] Supersede chain verified")

    # 8. Status change + summary -------------------------------------------
    await set_hypothesis_status(h.hypothesis_id, "SUPPORTED", "Strong evidence")
    summary = await get_case_summary(case.case_id)
    assert summary["leading_hypothesis"] is not None
    assert summary["leading_hypothesis"]["hypothesis_id"] == h.hypothesis_id
    assert summary["counts"]["evidence_total"] >= 3
    assert summary["counts"]["claims"] >= 1
    assert summary["counts"]["hypotheses"] == 1
    assert summary["counts"]["events"] >= 6
    print(f"[8/8] Summary: {summary['counts']}")

    print("\n✅ Part 2 verification PASSED")


if __name__ == "__main__":
    asyncio.run(main())