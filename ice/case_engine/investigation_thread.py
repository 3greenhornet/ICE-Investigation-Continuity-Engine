"""case_engine/investigation_thread.py — persistent unresolved work.

Owner: investigation logic layer (Part 3, section 3). Replaces the legacy
``engine/pursuits.py`` PursuitRegistry semantics for case mode.
Purpose: track unresolved investigation questions with strength decay and
semantic reinforcement. The "debt" formula surfaces neglected threads so the
next-best-action layer can prioritize them.

Downstream dependencies (allowed):
    - db.connection.get_pool
    - case_engine._emit_event
    - case_engine.exceptions (typed errors)
    - case_engine.schemas (InvestigationThread)
    - engine.memory.embed (embedding substrate for semantic reinforcement)
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import (
    DatabaseUnavailableError, ThreadNotFoundError
)
from case_engine.schemas import InvestigationThread
from engine.memory import embed

logger = logging.getLogger(__name__)

DECAY_FACTOR = 0.95
REINFORCE_DELTA = 0.08
REINFORCE_TOP3 = 0.04
DEAD_THRESHOLD = 0.10
MAX_AGE_CAP_DAYS = 30.0
SIMILARITY_THRESHOLD = 0.75


def _row_to_thread(row) -> InvestigationThread:
    """Map an asyncpg Record from ``investigation_threads`` to the model."""
    return InvestigationThread(
        thread_id=row["thread_id"],
        case_id=row["case_id"],
        question=row["question"],
        origin=row["origin"],
        created_at=row["created_at"],
        last_progress_at=row["last_progress_at"],
        strength=row["strength"],
        decision_impact=row["decision_impact"],
        uncertainty=row["uncertainty"],
        blocking_question_id=row["blocking_question_id"],
        reinforcement_history=list(row["reinforcement_history"] or []),
        is_active=row["is_active"],
    )


class InvestigationThreadRegistry:
    """In-memory cache with Postgres persistence."""

    def __init__(self, case_id: str):
        self.case_id = case_id
        self._threads: dict[str, InvestigationThread] = {}
        self._dirty_ids: set[str] = set()
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        """Load active threads from DB once.

        Postconditions:
            self._threads contains all rows with case_id == self.case_id
            and is_active == TRUE.
        Raises: DatabaseUnavailableError.
        """
        if self._loaded:
            return
        pool = await get_pool()
        if pool is None:
            raise DatabaseUnavailableError("Cannot load threads: no DB pool")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM investigation_threads
                WHERE case_id = $1 AND is_active = TRUE
                """,
                self.case_id,
            )
        for row in rows:
            self._threads[row["thread_id"]] = _row_to_thread(row)
        self._loaded = True

    async def create_thread(
        self,
        question: str,
        origin: str,
        decision_impact: float = 0.5,
        uncertainty: float = 0.5,
        blocking_question_id: Optional[str] = None,
    ) -> InvestigationThread:
        """Create a thread.

        Postconditions:
            A row exists in investigation_threads with strength 0.5.
            Cached.
            investigation_thread_created event emitted.
        Raises:
            ValueError on empty question or bad floats.
            DatabaseUnavailableError.
        Side effects:
            INSERT INTO investigation_threads (1).
            _emit_event("investigation_thread_created", {...}).
        """
        if not question or not question.strip():
            raise ValueError("question must be non-empty")
        if not (0.0 <= decision_impact <= 1.0):
            raise ValueError("decision_impact must be in [0, 1]")
        if not (0.0 <= uncertainty <= 1.0):
            raise ValueError("uncertainty must be in [0, 1]")

        pool = await get_pool()
        if pool is None:
            raise DatabaseUnavailableError("Cannot create thread: no DB pool")

        thread_id = f"thread_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO investigation_threads (
                    thread_id, case_id, question, origin,
                    created_at, last_progress_at,
                    strength, decision_impact, uncertainty,
                    blocking_question_id, reinforcement_history, is_active
                ) VALUES ($1, $2, $3, $4, $5, $5, 0.5, $6, $7, $8, '{}', TRUE)
                """,
                thread_id, self.case_id, question.strip(), origin,
                now, decision_impact, uncertainty, blocking_question_id,
            )

        await _emit_event(self.case_id, "investigation_thread_created", {
            "thread_id": thread_id,
            "question": question.strip(),
            "origin": origin,
        })

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM investigation_threads WHERE thread_id = $1", thread_id
            )
        thread = _row_to_thread(row)
        self._threads[thread_id] = thread
        self._loaded = True
        return thread

    async def reinforce(
        self,
        thread_id: str,
        current_turn: int,
        delta: float = REINFORCE_DELTA,
    ) -> InvestigationThread:
        """Increase thread strength. Capped at 1.0.

        Postconditions:
            strength = min(1.0, old_strength + delta).
            last_progress_at == now.
            reinforcement_history ends with current_turn (capped at the last
            100 entries).
        Raises: ThreadNotFoundError if the thread is not cached or inactive.
        Side effects:
            UPDATE investigation_threads (1).
            _emit_event("investigation_thread_reinforced", {...}).
        """
        thread = self._threads.get(thread_id)
        if thread is None or not thread.is_active:
            raise ThreadNotFoundError(f"Active thread not found: {thread_id}")

        new_strength = min(1.0, thread.strength + delta)
        history = thread.reinforcement_history + [current_turn]
        if len(history) > 100:
            history = history[-100:]

        now = datetime.now(timezone.utc)
        pool = await get_pool()
        if pool is None:
            raise DatabaseUnavailableError("Cannot reinforce: no DB pool")
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE investigation_threads
                SET strength = $1, last_progress_at = $2, reinforcement_history = $3::int[]
                WHERE thread_id = $4
                """,
                new_strength, now, history, thread_id,
            )

        thread.strength = new_strength
        thread.reinforcement_history = history
        thread.last_progress_at = now

        await _emit_event(self.case_id, "investigation_thread_reinforced", {
            "thread_id": thread_id,
            "new_strength": new_strength,
            "delta": delta,
        })
        return thread

    async def decay(self, current_turn: int) -> list[str]:
        """Decay all cached active threads. Deactivate those below threshold.

        Returns the list of deactivated thread IDs.
        Raises: DatabaseUnavailableError.
        Side effects:
            UPDATE investigation_threads (N rows).
            _emit_event("investigation_thread_deactivated", {...}) per
            deactivated thread.
        """
        deactivated: list[str] = []
        pool = await get_pool()
        if pool is None:
            raise DatabaseUnavailableError("Cannot decay: no DB pool")

        for thread_id, thread in list(self._threads.items()):
            if not thread.is_active:
                continue
            new_strength = thread.strength * DECAY_FACTOR
            is_active = new_strength >= DEAD_THRESHOLD
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE investigation_threads
                    SET strength = $1, is_active = $2
                    WHERE thread_id = $3
                    """,
                    new_strength, is_active, thread_id,
                )
            thread.strength = new_strength
            thread.is_active = is_active

            if not is_active:
                deactivated.append(thread_id)
                await _emit_event(self.case_id, "investigation_thread_deactivated", {
                    "thread_id": thread_id,
                    "final_strength": new_strength,
                })

        return deactivated

    def compute_debt(self, thread: InvestigationThread) -> float:
        """Return debt = strength x min(age_days, 30) x decision_impact x uncertainty.

        age_days uses (now - last_progress_at).total_seconds() / 86400,
        floored at 0.01 days, and capped at MAX_AGE_CAP_DAYS (30).
        Sync by design: pure in-memory arithmetic, no I/O.
        """
        now = datetime.now(timezone.utc)
        age_seconds = max(0.0, (now - thread.last_progress_at).total_seconds())
        age_days = max(0.01, age_seconds / 86400.0)
        return (
            thread.strength
            * min(age_days, MAX_AGE_CAP_DAYS)
            * thread.decision_impact
            * thread.uncertainty
        )

    async def get_active_by_debt(self, limit: int = 10) -> list[InvestigationThread]:
        """Return active threads sorted by debt descending."""
        await self._ensure_loaded()
        scored = [
            (self.compute_debt(t), t)
            for t in self._threads.values() if t.is_active
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:limit]]

    async def get_all_active(self) -> list[InvestigationThread]:
        """Return all cached active threads (loads from DB on first call)."""
        await self._ensure_loaded()
        return [t for t in self._threads.values() if t.is_active]

    async def flush_updates(self) -> None:
        """Persist any dirty thread IDs. Kept for API symmetry; currently
        the class writes eagerly, so this is a no-op unless a future change
        batches writes."""
        self._dirty_ids.clear()


async def _reinforce_from_winner_lexical(
    registry: "InvestigationThreadRegistry",
    winner_content: str,
    current_turn: int,
    threshold: float,
) -> Optional[str]:
    """Lexical twin of reinforce_from_winner (embedding-unavailable path).

    Similarity = Jaccard overlap of salient tokens; the same threshold
    semantics apply (>= threshold reinforces).
    """
    import re as _re

    def _salient(text: str) -> set[str]:
        stop = {
            "the", "and", "for", "was", "were", "with", "that", "this",
            "from", "investigate", "a", "an", "of", "to", "in", "on",
        }
        return {
            t for t in _re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) > 2 and t not in stop
        }

    winner_tokens = _salient(winner_content)
    if not winner_tokens:
        return None
    best_thread, best_sim = None, 0.0
    for t in registry._threads.values():
        if not t.is_active:
            continue
        t_tokens = _salient(t.question)
        if not t_tokens:
            continue
        sim = len(winner_tokens & t_tokens) / len(winner_tokens | t_tokens)
        if sim > best_sim:
            best_thread, best_sim = t, sim
    if best_thread is None or best_sim < threshold:
        return None
    await registry.reinforce(best_thread.thread_id, current_turn)
    return best_thread.thread_id


async def reinforce_from_winner(
    registry: InvestigationThreadRegistry,
    winner_content: str,
    current_turn: int,
    threshold: float = SIMILARITY_THRESHOLD,
) -> Optional[str]:
    """Find the active thread semantically closest to winner_content.

    Preconditions:
        winner_content is non-empty.
    Postconditions:
        If best similarity >= threshold -> reinforces that thread, returns its ID.
        Else -> returns None, no event emitted.
    Side effects:
        Reinforce emits its own event.
    """
    if not winner_content or not winner_content.strip():
        return None

    await registry._ensure_loaded()
    active = [t for t in registry._threads.values() if t.is_active]
    if not active:
        return None

    try:
        winner_emb = await embed(winner_content)
    except Exception as exc:
        # Hari ICE improvements pass: offline fallback — when no embedding
        # provider is configured, fall back to LEXICAL similarity (token
        # overlap) instead of returning None. Thread reinforcement keeps
        # working offline (verify_part3 [7]); the semantic version takes
        # over automatically when embeddings return.
        logger.debug("Winner embedding failed; lexical fallback: %s", exc)
        return await _reinforce_from_winner_lexical(
            registry, winner_content, current_turn, threshold
        )

    import numpy as np
    winner_vec = np.array(winner_emb, dtype=np.float32)
    winner_vec = winner_vec / (np.linalg.norm(winner_vec) + 1e-8)

    best_id: Optional[str] = None
    best_sim = 0.0
    for thread in active:
        try:
            t_emb = await embed(thread.question)
        except Exception:
            continue
        t_vec = np.array(t_emb, dtype=np.float32)
        t_vec = t_vec / (np.linalg.norm(t_vec) + 1e-8)
        sim = float(np.dot(winner_vec, t_vec))
        if sim > best_sim:
            best_sim = sim
            best_id = thread.thread_id

    if best_id is not None and best_sim >= threshold:
        await registry.reinforce(best_id, current_turn)
        return best_id
    return None