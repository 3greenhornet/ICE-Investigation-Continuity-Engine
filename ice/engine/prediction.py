"""
engine/prediction.py — Deterministic prediction error using cosine similarity.
No LLM calls. Local, fast, observable.
"""

import math
import logging
from typing import List

from engine.memory import embed

logger = logging.getLogger(__name__)

async def compute_prediction_error(
    last_assistant_response: str,
    current_user_input: str
) -> float:
    """
    Compute prediction error as 1 - cosine_similarity(embed(last), embed(current)).
    Returns 0.0 (no surprise) to 1.0 (complete surprise).
    """
    if not last_assistant_response or not current_user_input:
        return 0.5

    try:
        emb_expected = await embed(last_assistant_response)
        emb_actual = await embed(current_user_input)
        similarity = _cosine_similarity(emb_expected, emb_actual)
        error = 1.0 - similarity
        return max(0.0, min(1.0, error))
    except Exception as e:
        logger.error(f"Prediction error failed: {e}", exc_info=True)
        return 0.5

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def compute_investigation_surprise(
    evidence_content: str,
    hypothesis_statement: str,
) -> float:
    """
    ICE extension (inversion map, engine/prediction.py row + audit gap #5):
    evidence surprise — how unexpected a piece of evidence is relative to a
    hypothesis — as distinct from conversation surprise
    (compute_prediction_error). Deterministic, no LLM calls.

    Returns 1 - cosine_similarity(embed(evidence), embed(hypothesis)),
    clamped to [0.0, 1.0]. Returns 0.5 on embedding failure or empty input.
    """
    if not evidence_content or not hypothesis_statement:
        return 0.5
    try:
        emb_evidence = await embed(evidence_content)
        emb_hypothesis = await embed(hypothesis_statement)
        similarity = _cosine_similarity(emb_evidence, emb_hypothesis)
        return max(0.0, min(1.0, 1.0 - similarity))
    except Exception as e:
        logger.error(f"Investigation surprise failed: {e}", exc_info=True)
        return 0.5