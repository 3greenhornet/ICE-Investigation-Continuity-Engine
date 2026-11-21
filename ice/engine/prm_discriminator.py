"""
engine/prm_discriminator.py — Active-Inference Process Reward Model (PRM).

RECONSTRUCTION NOTE (see project worklog, tasks 3-b / fin-*):
The original repository snapshot imported this module from
``engine/generate.py::_generate_dialogue_with_prm`` but the file itself was
never part of the uploaded snapshot, so every call raised
``ModuleNotFoundError``.  This module is a minimal, self-consistent
implementation of the exact call-site contract:

    prm = ActiveInferencePRM(alpha=1.0, beta=0.0, ...)        # generate.py uses (1.0, 0.5, 0.8)
    best_candidate, lowest_fe = prm.select_best_candidate(
        candidates,            # list[str] — candidate dialogue utterances
        candidate_embeddings,  # list[list[float]] — one embedding per candidate
        winner_embedding,      # list[float] — embedding of the winning thought
        user_embedding,        # list[float] — embedding of the user input
        curiosity,             # float — agent curiosity drive [0, 1]
        volition_intensity,    # float — urgency of the volitional source [0, 1]
    )

Scoring is an active-inference style expected free energy: each candidate is
penalised by its sensory divergence from the winning internal thought (goal
congruence, weighted by ``alpha``) and from the conversational context
(social grounding, weighted by ``beta``), and rewarded by the epistemic
value of expressing the winning thought (curiosity bonus scaled by ``mu``).
The candidate with the LOWEST free energy is selected.

This path is legacy and disabled by default (ENABLE_PRM=false, see
engine/config.py); it is retained for future work, exactly as documented in
engine/generate.py's PRM hook.
"""

from typing import List, Sequence, Tuple
import math


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity with zero-safe guards; returns 0.0 on degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


class ActiveInferencePRM:
    """Selects the best dialogue candidate by minimising expected free energy.

    Parameters
    ----------
    alpha : float
        Precision weight on goal congruence — divergence from the winning
        internal thought (the candidate must verbalise that thought).
    beta : float
        Precision weight on social grounding — divergence from the user's
        contribution (the candidate must remain a coherent reply).
    mu : float
        Prior mean strength of the epistemic (curiosity) value of voicing the
        winning thought; scales the reward term, NOT a free parameter of the
        divergence terms.
    """

    def __init__(self, alpha: float = 1.0, beta: float = 0.5, mu: float = 0.8) -> None:
        if alpha < 0 or beta < 0:
            raise ValueError("alpha and beta must be non-negative")
        self.alpha = float(alpha)
        self.beta = float(beta)
        self.mu = float(mu)

    def free_energy(
        self,
        candidate_embedding: Sequence[float],
        winner_embedding: Sequence[float],
        user_embedding: Sequence[float],
        curiosity: float,
        volition_intensity: float,
    ) -> float:
        """Expected free energy of a single candidate utterance.

        FE = alpha * (1 - cos(cand, winner))      # goal-congruence divergence
           + beta  * (1 - cos(cand, user))       # social-grounding divergence
           - mu   * (curiosity * cos(cand, winner) + volition_intensity * 0.5 * cos(cand, user))
        """
        goal_congruence = _cosine_similarity(candidate_embedding, winner_embedding)
        social_grounding = _cosine_similarity(candidate_embedding, user_embedding)
        epistemic_value = (
            max(0.0, float(curiosity)) * goal_congruence
            + 0.5 * max(0.0, float(volition_intensity)) * social_grounding
        )
        return (
            self.alpha * (1.0 - goal_congruence)
            + self.beta * (1.0 - social_grounding)
            - self.mu * epistemic_value
        )

    def select_best_candidate(
        self,
        candidates: List[str],
        candidate_embeddings: List[List[float]],
        winner_embedding: Sequence[float],
        user_embedding: Sequence[float],
        curiosity: float,
        volition_intensity: float,
    ) -> Tuple[str, float]:
        """Return ``(best_candidate, lowest_free_energy)``.

        Raises ValueError on empty or length-mismatched input.
        """
        if not candidates:
            raise ValueError("select_best_candidate: no candidates provided")
        if len(candidates) != len(candidate_embeddings):
            raise ValueError(
                f"select_best_candidate: {len(candidates)} candidates but "
                f"{len(candidate_embeddings)} embeddings"
            )
        best_index = 0
        best_fe = float("inf")
        for i, embedding in enumerate(candidate_embeddings):
            fe = self.free_energy(
                embedding,
                winner_embedding,
                user_embedding,
                curiosity,
                volition_intensity,
            )
            if fe < best_fe:
                best_fe = fe
                best_index = i
        return candidates[best_index], best_fe