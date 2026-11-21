"""Mechanism 3: anti-repetition.

Two layers:

* a HARD seen-set -- anything already shown is excluded, period;
* a SOFT semantic refractory -- a faithful port of the Hari engine's
  ``engine/semantic_refractory.py`` idea (per the improvements document's
  "recover the underlying idea, not the old implementation" ruling):
  items whose embedding is cosine-close to something recently shown get
  their score multiplied down; the suppression decays linearly over
  ``refractory_turns`` and disappears entirely after the window.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np


class AntiRepetition:
    def __init__(
        self,
        refractory_turns: int = 4,
        similarity_threshold: float = 0.55,
        max_penalty: float = 0.65,
        seen_memory: int = 10_000,
    ) -> None:
        self.refractory_turns = int(refractory_turns)
        self.similarity_threshold = float(similarity_threshold)
        self.max_penalty = float(max_penalty)
        self._seen: list[str] = []          # insertion-ordered for the cap
        self._seen_set: set[str] = set()
        self._seen_memory = int(seen_memory)
        # (turn, unit_embedding, item_id) -- the Hari structure, verbatim
        self._suppressed: list[tuple[int, np.ndarray, str]] = []

    # ── hard layer ───────────────────────────────────────────────────────
    def is_seen(self, item_id: str) -> bool:
        return item_id in self._seen_set

    def mark_seen(self, item_id: str, embedding: np.ndarray | None, turn: int) -> None:
        if item_id in self._seen_set:
            return
        self._seen_set.add(item_id)
        self._seen.append(item_id)
        if len(self._seen) > self._seen_memory:
            evicted = self._seen.pop(0)
            self._seen_set.discard(evicted)
        if embedding is not None:
            vector = np.asarray(embedding, dtype=np.float32).ravel()
            norm = np.linalg.norm(vector)
            if norm > 0:
                self._suppressed.append((int(turn), vector / (norm + 1e-8), item_id))

    def filter_unseen(self, item_ids: Iterable[str]) -> list[str]:
        return [item_id for item_id in item_ids if item_id not in self._seen_set]

    # ── soft layer (Hari port) ───────────────────────────────────────────
    def prune(self, current_turn: int) -> None:
        self._suppressed = [
            (turn, embedding, item_id)
            for (turn, embedding, item_id) in self._suppressed
            if current_turn - turn <= self.refractory_turns
        ]

    def penalty_multiplier(
        self, candidate_id: str, candidate_embedding: np.ndarray | None, turn: int
    ) -> float:
        """Return a multiplier in ``[1 - max_penalty, 1.0]``."""
        if candidate_embedding is None:
            return 1.0
        vector = np.asarray(candidate_embedding, dtype=np.float32).ravel()
        norm = np.linalg.norm(vector)
        if norm == 0:
            return 1.0
        candidate = vector / (norm + 1e-8)
        penalty = 1.0
        for shown_turn, embedding, item_id in self._suppressed:
            if item_id == candidate_id:
                continue
            age = turn - shown_turn
            if age <= 0 or age > self.refractory_turns:
                continue
            similarity = float(np.dot(candidate, embedding))
            if similarity >= self.similarity_threshold:
                strength = 1.0 - (age - 1) / max(1, self.refractory_turns)
                strength = max(0.0, min(1.0, strength))
                penalty *= 1.0 - self.max_penalty * strength
        return max(1.0 - self.max_penalty, penalty)

    def penalize(
        self,
        scored: Sequence[tuple[str, float]],
        embeddings: dict[str, np.ndarray],
        turn: int,
    ) -> list[tuple[str, float]]:
        """Rescore (item_id, score) pairs with refractory multipliers."""
        return [
            (item_id, score * self.penalty_multiplier(item_id, embeddings.get(item_id), turn))
            for item_id, score in scored
        ]

    # ── introspection ────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        return {
            "seen_count": len(self._seen_set),
            "suppressed_count": len(self._suppressed),
            "suppressed": [
                {"item_id": item_id, "turn": turn}
                for (turn, _embedding, item_id) in self._suppressed
            ],
        }
