"""case_engine/timeline.py — temporal reconstruction and reasoning.

Owner:      Hari ICE "addition and improvements" pass (audit gap:
            "The service was healthy at 10:00. Unavailable at 10:05. The
            deployment completed at 10:02. These claims do not contradict
            each other — the important task is to reconstruct the sequence
            and determine whether the deployment explains the state change.
            That requires temporal and causal structure, not only pairwise
            contradiction classification.")
Purpose:    Reconstruct an ordered case timeline from every temporal signal
            available (evidence observed_at / valid_from / valid_until,
            claim valid_from / valid_until, ISO timestamps found inside
            content), then answer the questions a pairwise contradiction
            classifier cannot:
              * ordering: what happened between event A and event B?
              * state changes: which candidate events fall inside the
                window between a "state X" observation and a "state Y"
                observation — the suspect set for "what changed"?
              * consistency: do any two temporal claims overlap when they
                claim mutually exclusive states?
Read-only module: no events, no mutations.
Deps:       db.connection, case_engine.exceptions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

# ISO-ish timestamp patterns found in free text (deterministic extraction).
_TS_PATTERNS = [
    # 2026-09-20T14:02:00Z / +02:00 / .123
    re.compile(
        r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?"
        r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\b"
    ),
    # 2026-09-20 14:02
    re.compile(r"\b(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\b"),
    # 2026-09-20
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    # 14:02 / 14:02:00 (time-only; anchored to the evidence's own date when
    # one exists — see _parse_time_only)
    re.compile(r"\b(\d{1,2}:\d{2}(:\d{2})?)\b"),
]

_STATE_CUES = {
    "healthy": "healthy",
    "unavailable": "unavailable",
    "degraded": "degraded",
    "healthy again": "healthy",
    "recovered": "healthy",
    "outage": "unavailable",
    "down": "unavailable",
    "failure": "failing",
    "failing": "failing",
    "errors": "failing",
    "normal": "normal",
    "stable": "stable",
}


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_ts(raw: str, default_date: Optional[datetime] = None) -> Optional[datetime]:
    """Best-effort deterministic ISO timestamp parser."""
    raw = raw.strip()
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", raw) and default_date:
            h, m, *rest = raw.split(":")
            base = default_date
            dt = base.replace(hour=int(h), minute=int(m),
                              second=int(rest[0]) if rest else 0)
            return _ensure_utc(dt)
        return _ensure_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except Exception:
        return None


@dataclass
class TimelineEntry:
    """One located point (or interval) on the case timeline."""

    label: str
    ts: Optional[datetime]
    ts_end: Optional[datetime] = None
    source_type: str = "evidence"          # evidence | claim | content_ts
    source_id: str = ""
    state: Optional[str] = None            # healthy/unavailable/... when detectable
    content_excerpt: str = ""
    origin: str = ""                       # observed_at | valid_window | content

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "ts": self.ts.isoformat() if self.ts else None,
            "ts_end": self.ts_end.isoformat() if self.ts_end else None,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "state": self.state,
            "excerpt": self.content_excerpt[:160],
            "origin": self.origin,
        }


def _detect_state(text: str) -> Optional[str]:
    low = text.lower()
    for cue, state in _STATE_CUES.items():
        if cue in low:
            return state
    return None


async def build_case_timeline(case_id: str) -> list[TimelineEntry]:
    """Reconstruct the ordered timeline (ascending ts; undated at the end).

    Signals, in reliability order:
        1. evidence.observed_at (direct observation time)
        2. evidence.valid_from..valid_until (validity window)
        3. claim.valid_from..valid_until
        4. ISO timestamps parsed out of evidence CONTENT (origin='content',
           the weakest provenance — recorded but flagged)
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot build timeline: no DB pool")

    entries: list[TimelineEntry] = []
    async with pool.acquire() as conn:
        ev_rows = await conn.fetch(
            """
            SELECT evidence_id, content, observed_at, valid_from,
                   valid_until, recorded_at, evidence_type
            FROM evidence
            WHERE case_id = $1 AND status IN ('ACTIVE', 'STALE')
            """,
            case_id,
        )
        claim_rows = await conn.fetch(
            """
            SELECT claim_id, statement, valid_from, valid_until, created_at
            FROM claims WHERE case_id = $1 AND status = 'ACTIVE'
            """,
            case_id,
        )

    for r in ev_rows:
        state = _detect_state(r["content"])
        if r["observed_at"] is not None:
            entries.append(
                TimelineEntry(
                    label=f"evidence {r['evidence_id']}",
                    ts=_ensure_utc(r["observed_at"]),
                    source_type="evidence",
                    source_id=r["evidence_id"],
                    state=state,
                    content_excerpt=r["content"],
                    origin="observed_at",
                )
            )
        if r["valid_from"] is not None or r["valid_until"] is not None:
            entries.append(
                TimelineEntry(
                    label=f"validity {r['evidence_id']}",
                    ts=_ensure_utc(r["valid_from"]) if r["valid_from"] else None,
                    ts_end=_ensure_utc(r["valid_until"]) if r["valid_until"] else None,
                    source_type="evidence",
                    source_id=r["evidence_id"],
                    state=state,
                    content_excerpt=r["content"],
                    origin="valid_window",
                )
            )
        # timestamps parsed from content
        seen_in_item = 0
        for pattern in _TS_PATTERNS[:3]:
            for match in pattern.finditer(r["content"] or ""):
                ts = _parse_ts(match.group(1))
                if ts is None:
                    continue
                entries.append(
                    TimelineEntry(
                        label=f"content ts ({r['evidence_id']})",
                        ts=ts,
                        source_type="content_ts",
                        source_id=r["evidence_id"],
                        state=_detect_state(
                            (r["content"] or "")[
                                max(0, match.start() - 60): match.end() + 60
                            ]
                        ),
                        content_excerpt=r["content"],
                        origin="content",
                    )
                )
                seen_in_item += 1
            if seen_in_item:
                break  # prefer full-date matches; skip time-only fallback

    for r in claim_rows:
        if r["valid_from"] is not None or r["valid_until"] is not None:
            entries.append(
                TimelineEntry(
                    label=f"claim window {r['claim_id']}",
                    ts=_ensure_utc(r["valid_from"]) if r["valid_from"] else None,
                    ts_end=_ensure_utc(r["valid_until"]) if r["valid_until"] else None,
                    source_type="claim",
                    source_id=r["claim_id"],
                    state=_detect_state(r["statement"]),
                    content_excerpt=r["statement"],
                    origin="claim_valid_window",
                )
            )

    dated = [e for e in entries if e.ts is not None]
    undated = [e for e in entries if e.ts is None]
    dated.sort(key=lambda e: e.ts)
    return dated + undated


async def events_between(
    case_id: str,
    start: datetime,
    end: datetime,
    exclude_source_ids: tuple[str, ...] = (),
) -> list[TimelineEntry]:
    """Timeline entries strictly inside the (start, end) window.

    The "suspect set" for a state change: everything that happened between
    the last healthy observation and the first failing one.
    """
    timeline = await build_case_timeline(case_id)
    out = []
    for e in timeline:
        if e.ts is None or e.source_id in exclude_source_ids:
            continue
        if start < e.ts < end:
            out.append(e)
    return out


async def explain_state_change(case_id: str) -> dict:
    """Locate state transitions and the candidate events between them.

    Finds the last 'healthy/normal/stable' entry followed by a later
    'unavailable/degraded/failing' entry, then returns every dated entry
    in that window as a candidate cause — the temporal answer to "did the
    deployment explain the state change".
    """
    timeline = await build_case_timeline(case_id)
    good_states = {"healthy", "normal", "stable"}
    bad_states = {"unavailable", "degraded", "failing"}

    last_good: Optional[TimelineEntry] = None
    first_bad: Optional[TimelineEntry] = None
    for e in timeline:
        if e.ts is None or e.state is None:
            continue
        if e.state in good_states:
            last_good = e
            first_bad = None  # reset: a later healthy observation restarts
        elif e.state in bad_states and first_bad is None:
            if last_good is not None:
                first_bad = e
                break

    if last_good is None or first_bad is None:
        return {
            "transition_found": False,
            "from": None,
            "to": None,
            "candidate_events": [],
            "note": "No healthy→failing transition located on the timeline",
        }

    candidates = await events_between(
        case_id, last_good.ts, first_bad.ts,
        exclude_source_ids=(last_good.source_id, first_bad.source_id),
    )
    return {
        "transition_found": True,
        "from": last_good.to_dict(),
        "to": first_bad.to_dict(),
        "candidate_events": [c.to_dict() for c in candidates],
        "window_seconds": (first_bad.ts - last_good.ts).total_seconds(),
        "note": (
            "Candidate events are ordered observations inside the transition "
            "window; temporal adjacency is NOT causation — pair this with "
            "case_engine.causal before drawing conclusions."
        ),
    }


async def check_temporal_consistency(case_id: str) -> dict:
    """Detect claims whose validity windows overlap while asserting
    mutually exclusive states of the same apparent subject.

    Returns {conflicts: [{a, b, overlap_seconds}], checked_pairs: n}.
    Overlap + conflicting states is evidence for a change-over-time
    resolution of an apparent contradiction (contradiction.relation_type
    'temporal_mismatch') rather than a genuine conflict.
    """
    timeline = await build_case_timeline(case_id)
    intervals = [
        e for e in timeline
        if e.ts is not None and e.ts_end is not None and e.state is not None
    ]
    conflicts = []
    checked = 0
    for i in range(len(intervals)):
        for j in range(i + 1, len(intervals)):
            a, b = intervals[i], intervals[j]
            if a.source_id == b.source_id:
                continue
            checked += 1
            overlap_start = max(a.ts, b.ts)
            overlap_end = min(a.ts_end, b.ts_end)
            if overlap_end > overlap_start and a.state != b.state:
                conflicts.append(
                    {
                        "a": a.to_dict(),
                        "b": b.to_dict(),
                        "overlap_seconds": (overlap_end - overlap_start).total_seconds(),
                    }
                )
    return {"conflicts": conflicts, "checked_pairs": checked}
