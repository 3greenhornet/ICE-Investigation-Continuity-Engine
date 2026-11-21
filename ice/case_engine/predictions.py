"""case_engine/predictions.py — testable prediction records.

Owner:      Hari ICE "addition and improvements" pass. Implements the
            "Prediction before explanation" direction and the Metaculus-style
            forecast mechanism from the improvements document:
            every meaningful hypothesis should predict something that has
            not yet been observed; when the world later answers, the
            prediction is scored. This is deliberately SEPARATE from
            hypothesis confidence — confidence is a planning belief, a
            prediction score is an empirical calibration record. The two
            must never be silently conflated ("A belief doesn't become less
            true because you haven't reviewed it", and a confidence value
            is not a probability).
Purpose:    CRUD + resolution scoring for hypothesis_predictions
            (migration 018 Part B) with full event provenance.
Deps:       db.connection, case_engine (_emit_event), case_engine.exceptions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

PREDICTION_STATUSES = ("OPEN", "CONFIRMED", "REFUTED", "UNRESOLVED", "VOID")


def _row_to_prediction(row) -> dict:
    return {
        "prediction_id": row["prediction_id"],
        "case_id": row["case_id"],
        "hypothesis_id": row["hypothesis_id"],
        "statement": row["statement"],
        "resolution_condition": row["resolution_condition"],
        "probability": float(row["probability"]),
        "expected_resolution": row["expected_resolution"],
        "status": row["status"],
        "observed_result": row["observed_result"],
        "score": float(row["score"]) if row["score"] is not None else None,
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
    }


async def create_prediction(
    case_id: str,
    hypothesis_id: str,
    statement: str,
    resolution_condition: str,
    probability: float = 0.5,
    expected_resolution: Optional[str] = None,
) -> dict:
    """Record one testable prediction for a hypothesis.

    Postconditions:
        hypothesis_predictions row created with status='OPEN'.
        hypothesis_prediction_created event emitted (transactional partner
        of the insert when a pool transaction is available).
    Raises:
        ValueError on empty statement / resolution_condition or probability
        outside [0, 1]; HypothesisNotFoundError when the hypothesis does not
        exist; DatabaseUnavailableError.
    """
    if not statement or not statement.strip():
        raise ValueError("prediction statement must be non-empty")
    if not resolution_condition or not resolution_condition.strip():
        raise ValueError("resolution_condition must be non-empty")
    if not (0.0 <= probability <= 1.0):
        raise ValueError("probability must be in [0, 1]")

    from case_engine.hypothesis import get_hypothesis

    hypothesis = await get_hypothesis(hypothesis_id)
    if hypothesis.case_id != case_id:
        raise ValueError(
            f"hypothesis {hypothesis_id} does not belong to case {case_id}"
        )

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot create prediction: no DB pool")

    prediction_id = f"pred_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO hypothesis_predictions (
                    prediction_id, case_id, hypothesis_id, statement,
                    resolution_condition, probability, expected_resolution,
                    status, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'OPEN', $8)
                """,
                prediction_id, case_id, hypothesis_id, statement.strip(),
                resolution_condition.strip(), probability,
                expected_resolution, now,
            )
            await _emit_event(
                case_id,
                "hypothesis_prediction_created",
                {
                    "prediction_id": prediction_id,
                    "hypothesis_id": hypothesis_id,
                    "statement": statement.strip(),
                    "probability": probability,
                },
                conn=conn,
            )

    return await get_prediction(prediction_id)


async def get_prediction(prediction_id: str) -> dict:
    """Load one prediction. Raises KeyError-shaped ValueError when absent."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load prediction: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM hypothesis_predictions WHERE prediction_id = $1",
            prediction_id,
        )
    if row is None:
        raise ValueError(f"Prediction not found: {prediction_id}")
    return _row_to_prediction(row)


async def list_predictions(
    case_id: Optional[str] = None,
    hypothesis_id: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """List predictions, newest first, filterable."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot list predictions: no DB pool")
    conditions, params = ["TRUE"], []
    if case_id:
        params.append(case_id)
        conditions.append(f"case_id = ${len(params)}")
    if hypothesis_id:
        params.append(hypothesis_id)
        conditions.append(f"hypothesis_id = ${len(params)}")
    if status:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM hypothesis_predictions WHERE "
            f"{' AND '.join(conditions)} ORDER BY created_at DESC",
            *params,
        )
    return [_row_to_prediction(r) for r in rows]


def brier_score(probability: float, confirmed: bool) -> float:
    """Brier score for a binary prediction: (p − outcome)^2.

    0.0 = perfect, 0.25 = always-say-0.5 baseline, 1.0 = perfectly wrong.
    Returned predictions store score = 1 − brier (higher is better) to stay
    consistent with ICE's "higher score = better" conventions; the raw
    brier is in the payload.
    """
    outcome = 1.0 if confirmed else 0.0
    brier = (probability - outcome) ** 2
    return brier


async def resolve_prediction(
    prediction_id: str,
    status: str,
    observed_result: str,
) -> dict:
    """Resolve an OPEN prediction with the observed outcome.

    Args:
        status: CONFIRMED (the resolution condition was met), REFUTED
            (the opposite was observed), UNRESOLVED (checked, could not
            determine), VOID (the prediction became unanswerable).
        observed_result: what was actually observed (provenance text).

    Postconditions:
        Row updated with status, observed_result, score (1 − Brier for
        CONFIRMED/REFUTED) and resolved_at.
        hypothesis_prediction_resolved event emitted.
    Raises:
        ValueError when the prediction is not OPEN or the status is invalid.
    """
    if status not in PREDICTION_STATUSES:
        raise ValueError(f"Invalid prediction status: {status}")
    current = await get_prediction(prediction_id)
    if current["status"] != "OPEN":
        raise ValueError(
            f"Prediction {prediction_id} is already {current['status']}"
        )

    score = None
    brier = None
    if status in ("CONFIRMED", "REFUTED"):
        brier = brier_score(current["probability"], status == "CONFIRMED")
        score = round(1.0 - brier, 4)

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot resolve prediction: no DB pool")
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE hypothesis_predictions
                SET status = $1, observed_result = $2, score = $3,
                    resolved_at = $4
                WHERE prediction_id = $5
                """,
                status, observed_result.strip(), score, now, prediction_id,
            )
            await _emit_event(
                current["case_id"],
                "hypothesis_prediction_resolved",
                {
                    "prediction_id": prediction_id,
                    "hypothesis_id": current["hypothesis_id"],
                    "status": status,
                    "score": score,
                    "brier": round(brier, 4) if brier is not None else None,
                },
                conn=conn,
            )

    return await get_prediction(prediction_id)


async def prediction_calibration_report(case_id: Optional[str] = None) -> dict:
    """Aggregate resolution accuracy per hypothesis + overall Brier mean.

    The empirical answer to "were its expectations borne out" — a record of
    whether the system's predictions actually came true, kept strictly
    separate from confidence heuristics.
    """
    resolved = [
        p
        for p in await list_predictions(case_id=case_id)
        if p["status"] in ("CONFIRMED", "REFUTED") and p["score"] is not None
    ]
    if not resolved:
        return {
            "resolved_count": 0,
            "mean_score": None,
            "mean_brier": None,
            "confirmed": 0,
            "refuted": 0,
        }
    briers = [1.0 - p["score"] for p in resolved]
    return {
        "resolved_count": len(resolved),
        "mean_score": round(sum(p["score"] for p in resolved) / len(resolved), 4),
        "mean_brier": round(sum(briers) / len(briers), 4),
        "confirmed": sum(1 for p in resolved if p["status"] == "CONFIRMED"),
        "refuted": sum(1 for p in resolved if p["status"] == "REFUTED"),
    }
