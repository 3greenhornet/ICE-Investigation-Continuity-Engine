"""case_engine/claim.py — claim plane of the ICE case engine.

Owner: ICE revamp Part 2 §4 (implementation task ice-2-b).
Purpose: convert evidence into structured claims. Strictly extract claims only
— never infer. Claims are the middle layer between evidence and hypotheses.

Downstream deps:
    - case_engine.hypothesis (recompute joins claims via evidence_graph_edges)
    - case_engine.contradiction (Part 3; detect_contradiction consumes claim
      pairs produced by find_contradicting_claims)
    - case_engine.information_gap / evidence_graph / decision (Part 3)
    - app routes (Part 4) and scripts/verify_part2.py

Upstream deps (owned by ice-2-a / ice-2-b):
    - db.connection.get_pool
    - case_engine._emit_event
    - case_engine.exceptions (ClaimNotFoundError, DatabaseUnavailableError,
      EvidenceNotFoundError)
    - case_engine.llm._call_llm_json
    - case_engine.schemas (Claim, ClaimType)
    - case_engine.evidence.get_evidence (this repo, ice-2-b)
    - db/migrations/012_case_model.sql (claims table)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from db.connection import get_pool
from case_engine import _emit_event
from case_engine.exceptions import (
    ClaimNotFoundError,
    DatabaseUnavailableError,
    EvidenceNotFoundError,
)
from case_engine.llm import _call_llm_json
from case_engine.schemas import Claim, ClaimType
from case_engine.evidence import get_evidence

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a hard runtime dep
    import asyncpg

logger = logging.getLogger(__name__)

VALID_CLAIM_TYPES = {
    "MEASUREMENT",
    "OBSERVATION",
    "CORRELATION",
    "TEMPORAL_ASSOCIATION",
    "CAUSAL_CLAIM",
    "INTERPRETATION",
    "PREDICTION",
    "ASSUMPTION",
}

# ---------------------------------------------------------------------------
# §4.3 Claim extraction prompt (doubled braces: this is a .format template)
# ---------------------------------------------------------------------------

CLAIM_EXTRACTION_PROMPT = '''You are extracting structured claims from a piece of evidence.

EVIDENCE CONTENT:
"""
{evidence_content}
"""

EVIDENCE TYPE: {evidence_type}
CASE OBJECTIVE: {case_objective}

Extract all distinct claims from this evidence. A claim is a single proposition that could support or contradict a hypothesis.

For each claim, specify:
- statement: the claim in one clear sentence
- claim_type: one of MEASUREMENT, OBSERVATION, CORRELATION, TEMPORAL_ASSOCIATION, CAUSAL_CLAIM, INTERPRETATION, PREDICTION, ASSUMPTION
- scope: a dict of qualifiers like {{"region": "us-east", "service": "checkout"}} or null
- valid_from: ISO 8601 timestamp if the claim is time-bounded, else null
- valid_until: ISO 8601 timestamp if the claim is time-bounded, else null

Rules:
1. Extract ONLY claims directly supported by the evidence text.
2. Do NOT infer, extrapolate, or add background knowledge.
3. Do NOT create claims for opinions, hedges, or questions.
4. If a sentence contains multiple claims, split them.
5. If the evidence contains no extractable claims, return {{"claims": []}}.

Return ONLY a JSON object with this exact shape:
{{"claims": [{{"statement": "...", "claim_type": "...", "scope": {{...}} or null, "valid_from": null, "valid_until": null}}]}}
'''


# ---------------------------------------------------------------------------
# §4.4 Row mapping and ISO parsing
# ---------------------------------------------------------------------------

def _row_to_claim(row: asyncpg.Record) -> Claim:
    """Map an asyncpg row to Claim. Explicit column mapping."""
    # P3/I3 (merge spec Part 1 §5.3): jsonb columns can come back as raw
    # JSON strings (custom codec / JSONB->TEXT migration); parse defensively.
    raw_scope = row["scope"]
    if isinstance(raw_scope, str):
        try:
            scope_dict = json.loads(raw_scope)
        except Exception:
            scope_dict = None
    elif isinstance(raw_scope, dict):
        scope_dict = raw_scope
    else:
        scope_dict = None

    return Claim(
        claim_id=row["claim_id"],
        case_id=row["case_id"],
        statement=row["statement"],
        claim_type=row["claim_type"],
        status=row["status"],
        scope=scope_dict,
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        supporting_evidence_ids=list(row["supporting_evidence_ids"] or []),
        contradicting_evidence_ids=list(row["contradicting_evidence_ids"] or []),
        confidence=row["confidence"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _parse_iso(value: object) -> Optional[datetime]:
    """Parse an ISO 8601 string (or pass through a datetime), tz-aware.

    Preconditions: value is None, a datetime, or an ISO 8601 string.
    Postconditions: returns a timezone-aware datetime or None.
    Raises: nothing (unparseable input returns None).
    Side effects: none.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# §4.5 Direct claim creation
# ---------------------------------------------------------------------------

async def create_claim(
    case_id: str,
    statement: str,
    claim_type: ClaimType,
    scope: Optional[dict] = None,
    valid_from: Optional[datetime] = None,
    valid_until: Optional[datetime] = None,
    supporting_evidence_ids: Optional[list[str]] = None,
    confidence: float = 0.5,
) -> Claim:
    """Create a claim row directly (bypasses LLM extraction).

    Preconditions:
        case_id exists.
        statement is non-empty.
        claim_type is in VALID_CLAIM_TYPES.
        0.0 <= confidence <= 1.0.
    Postconditions:
        A claims row exists with a fresh claim_id.
    Raises:
        ValueError on any precondition violation.
        DatabaseUnavailableError.
    Side effects: INSERT INTO claims (1 row).
    """
    if not statement or not statement.strip():
        raise ValueError("statement must be non-empty")
    if claim_type not in VALID_CLAIM_TYPES:
        raise ValueError(f"Invalid claim_type: {claim_type}")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0, 1]")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot create claim: no DB pool")

    claim_id = f"c_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO claims (
                claim_id, case_id, statement, claim_type, scope,
                valid_from, valid_until,
                supporting_evidence_ids, contradicting_evidence_ids,
                confidence, status, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8::text[], '{}', $9, 'ACTIVE', $10, $10)
            """,
            claim_id,
            case_id,
            statement.strip(),
            claim_type,
            json.dumps(scope) if scope else None,
            valid_from,
            valid_until,
            supporting_evidence_ids or [],
            confidence,
            now,
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM claims WHERE claim_id = $1", claim_id)
    return _row_to_claim(row)


# ---------------------------------------------------------------------------
# §4.6 LLM claim extraction
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Hari ICE improvements pass — deterministic offline claim extraction.
#
# The second-pass audit noted the benchmark "was not run" because the
# environment lacked LLM credentials — the engine simply could not run
# end-to-end offline. This fallback implements the deterministic half of
# the Robin lesson (a deterministic pipeline with intelligent steps): when
# no provider is configured, claims are extracted by sentence segmentation
# + keyword claim-type classification. It is a REAL engine capability
# (every investigation step runs without network), not a benchmark-only
# stub; quality is lower than the LLM path and it is recorded as such
# (origin "deterministic_fallback" in claim scope).
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")

_CLAIM_TYPE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("MEASUREMENT", (
        "ms", "%", "gb", "mb", "kb", "qps", "rpm", "p95", "p99", "cpu",
        "memory", "latency", "throughput", "count", "rate", "seconds",
        "minutes", "hours", "max_connections", "active", "wait",
    )),
    ("TEMPORAL_ASSOCIATION", (
        "after", "before", "during", "since", "at 10:", "window",
        "deployment", "rollout", "started", "began", "until",
    )),
    ("CAUSAL_CLAIM", (
        "caused", "because", "due to", "resulted in", "led to",
        "root cause", "triggered", "broke",
    )),
    ("CORRELATION", (
        "correlates", "along with", "together with", "whenever",
        "coincided", "associated with",
    )),
    ("ASSUMPTION", (
        "assume", "assumption", "presumably", "expected", "should be",
        "we believe", "likely",
    )),
    ("PREDICTION", (
        "will", "expect", "predict", "forecast", "anticipated",
    )),
    ("OBSERVATION", (
        "observed", "saw", "noticed", "found", "appears", "seems",
        "reported", "log", "logs", "error", "errors", "failed", "failing",
        "healthy", "unavailable",
    )),
]

_HEDGE_WORDS = frozenset(
    "maybe perhaps possibly might could note todo question".split()
)


def _classify_claim_offline(sentence: str) -> str:
    low = sentence.lower()
    for ctype, keywords in _CLAIM_TYPE_KEYWORDS:
        if any(k in low for k in keywords):
            return ctype
    return "OBSERVATION"


def _split_sentences_offline(content: str, max_claims: int = 8) -> list[str]:
    """Sentence-split evidence content into candidate claim statements."""
    sentences = [s.strip() for s in _SENT_SPLIT.split(content or "")]
    out: list[str] = []
    for s in sentences:
        # skip table headers, short fragments, hedges and questions
        if len(s) < 12:
            continue
        low = s.lower()
        if low.endswith("?"):
            continue
        if any(low.startswith(h) for h in _HEDGE_WORDS):
            continue
        # normalize CSV metric rows into readable claims
        if "," in s and any(c.isdigit() for c in s) and s.count(",") >= 2:
            s = "metric reading: " + s
        out.append(s[:400])
        if len(out) >= max_claims:
            break
    return out


async def extract_claims_deterministic(evidence_id: str) -> list[Claim]:
    """Deterministic claim extraction (no LLM). See the block comment above.

    Same contract as extract_claims: creates Claim rows, appends evidence.
    extracted_claim_ids, emits claim_extracted. Claim scope carries
    {"extraction": "deterministic_fallback"} so downstream consumers can
    distinguish provenance.
    """
    evidence = await get_evidence(evidence_id)

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot extract claims: no DB pool")

    sentences = _split_sentences_offline(evidence.content)
    created: list[Claim] = []

    for sentence in sentences:
        claim_type = _classify_claim_offline(sentence)
        try:
            claim = await create_claim(
                case_id=evidence.case_id,
                statement=sentence,
                claim_type=claim_type,  # type: ignore[arg-type]
                scope={"extraction": "deterministic_fallback"},
                supporting_evidence_ids=[evidence_id],
            )
        except Exception as exc:
            logger.debug("deterministic claim create failed: %s", exc)
            continue
        created.append(claim)

    # Append to evidence.extracted_claim_ids (array_cat: never overwrite)
    if created:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE evidence
                SET extracted_claim_ids = array_cat(extracted_claim_ids, $1::text[])
                WHERE evidence_id = $2
                """,
                [c.claim_id for c in created], evidence_id,
            )

    await _emit_event(
        evidence.case_id,
        "claim_extracted",
        {
            "evidence_id": evidence_id,
            "claim_ids": [c.claim_id for c in created],
            "claim_count": len(created),
            "extraction": "deterministic_fallback",
        },
    )
    return created


async def extract_claims(evidence_id: str) -> list[Claim]:
    """Extract structured claims from one evidence item via LLM.

    Preconditions: evidence_id exists.
    Postconditions:
        Zero or more Claim rows created.
        Evidence.extracted_claim_ids is appended (never overwritten).
        claim_extracted event emitted (even when count == 0).
        Best effort (Part 2 §10.3, fix 2I): for every created claim x every
        existing hypothesis, the optional NLI classifier proposes a
        SUPPORTS/CONTRADICTS edge into the proposed_edges table (status
        'pending'); nothing is committed to evidence_graph_edges without
        user confirmation, and NLI being unavailable changes nothing.
    Raises:
        EvidenceNotFoundError.
        DatabaseUnavailableError.
    Side effects:
        INSERT INTO claims (N rows).
        UPDATE evidence (1 row).
        INSERT INTO proposed_edges (0..N rows, best effort, no event).
        _emit_event("claim_extracted", {...}).
    """
    evidence = await get_evidence(evidence_id)

    # Fetch case objective for context
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot extract claims: no DB pool")
    async with pool.acquire() as conn:
        case_row = await conn.fetchrow(
            "SELECT objective FROM cases WHERE case_id = $1", evidence.case_id
        )
    case_objective = case_row["objective"] if case_row else ""

    # Hari ICE improvements pass: prompt centralisation — the template now
    # lives in case_engine.prompts (id "claim_extraction", v1, text
    # preserved verbatim); every LLM call records the prompt id/version in
    # the llm_ledger and runs on the EXTRACTOR role chain when configured.
    from case_engine.prompts import render as render_prompt
    _pid, _pver, prompt = render_prompt(
        "claim_extraction",
        evidence_content=evidence.content,
        evidence_type=evidence.evidence_type,
        case_objective=case_objective,
    )

    # Hari ICE improvements pass: offline fallback — when no LLM provider
    # is configured (_call_llm_json raises LLMUnavailableError) the
    # deterministic extractor runs instead of failing (the audit's "the
    # benchmark was not run" gap closes here). NOTE: the check is the
    # raised exception, not a pre-check, so injected test mocks of
    # _call_llm_json keep working.
    from case_engine.exceptions import LLMUnavailableError

    try:
        raw = await _call_llm_json(
            prompt,
            temperature=0.1,
            max_retries=3,
            role="EXTRACTOR",
            operation="claim_extraction",
            prompt_id=_pid,
            prompt_version=_pver,
            case_id=evidence.case_id,
        )
    except LLMUnavailableError:
        return await extract_claims_deterministic(evidence_id)

    if not raw or "claims" not in raw or not isinstance(raw["claims"], list):
        await _emit_event(
            evidence.case_id,
            "claim_extracted",
            {
                "evidence_id": evidence_id,
                "claim_ids": [],
                "claim_count": 0,
            },
        )
        return []

    created_claims: list[Claim] = []
    for item in raw["claims"]:
        if not isinstance(item, dict):
            continue
        statement = (item.get("statement") or "").strip()
        claim_type = item.get("claim_type")
        if not statement or claim_type not in VALID_CLAIM_TYPES:
            continue
        claim = await create_claim(
            case_id=evidence.case_id,
            statement=statement,
            claim_type=claim_type,  # type: ignore[arg-type]
            scope=item.get("scope") if isinstance(item.get("scope"), dict) else None,
            valid_from=_parse_iso(item.get("valid_from")),
            valid_until=_parse_iso(item.get("valid_until")),
            supporting_evidence_ids=[evidence_id],
        )
        created_claims.append(claim)

    # Append to evidence.extracted_claim_ids (array_cat: never overwrite)
    if created_claims:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE evidence
                SET extracted_claim_ids = array_cat(extracted_claim_ids, $1::text[])
                WHERE evidence_id = $2
                """,
                [c.claim_id for c in created_claims],
                evidence_id,
            )

    # Part 2 §10.3 (fix 2I): auto-propose claim->hypothesis edges via NLI
    # — best effort, user-confirmation middle ground. Everything is
    # guarded: NLI unavailable (transformers not installed) proposes
    # nothing, and a failed proposed_edges insert never breaks extraction.
    #
    # HONEST NOTE (doc Part 2 §12 shortcut #14): the stored confidence is a
    # flat 0.8, not the model's actual probability — the exact value is
    # discarded; 0.8 matches the 0.7 classification threshold
    # "high-confidence-but-not-certain". A better design would store the
    # real probability.
    #
    # EVENT DISCIPLINE (doc Part 2 §10.4, option 2): inserting a proposed
    # edge emits NO case_events row. proposed_edge_accepted/rejected are
    # not in the EVENT_TYPES allow-list and adding new event types is a
    # schema change deferred to Part 4 — the accept/reject handlers
    # (app-layer scope) log at INFO level only. This is a documented
    # deviation from "every mutation emits exactly one event".
    if created_claims:
        try:
            from case_engine.nli import classify_claim_against_hypothesis
            from case_engine.hypothesis import list_hypotheses_for_case

            try:
                hypotheses = await list_hypotheses_for_case(evidence.case_id)
            except Exception:
                hypotheses = []

            if hypotheses:
                for claim in created_claims:
                    for hypothesis in hypotheses:
                        relation = await classify_claim_against_hypothesis(
                            claim.statement, hypothesis.statement,
                        )
                        if relation is None or relation == "NEUTRAL":
                            continue  # NLI unavailable or genuinely neutral
                        # Propose the edge, don't commit it
                        try:
                            async with pool.acquire() as conn:
                                await conn.execute(
                                    """
                                    INSERT INTO proposed_edges (
                                        edge_id, case_id, from_node, from_type,
                                        to_node, to_type, relation, confidence,
                                        model_used, status
                                    ) VALUES ($1, $2, $3, 'claim', $4, 'hypothesis',
                                           $5, $6, 'roberta-large-mnli', 'pending')
                                    ON CONFLICT (case_id, from_node, to_node, relation)
                                    DO NOTHING
                                    """,
                                    f"proposed_{uuid.uuid4().hex[:12]}",
                                    evidence.case_id,
                                    claim.claim_id,
                                    hypothesis.hypothesis_id,
                                    relation.lower(),  # SUPPORTS -> supports
                                    0.8,  # flat confidence (shortcut #14)
                                )
                        except Exception as exc:
                            logger.debug("Proposed edge insert failed: %s", exc)
        except Exception as exc:
            logger.debug("NLI edge proposal skipped: %s", exc)

    await _emit_event(
        evidence.case_id,
        "claim_extracted",
        {
            "evidence_id": evidence_id,
            "claim_ids": [c.claim_id for c in created_claims],
            "claim_count": len(created_claims),
        },
    )
    return created_claims


# ---------------------------------------------------------------------------
# §4.7 Evidence <-> claim linking
# ---------------------------------------------------------------------------

async def link_evidence_to_claim(
    claim_id: str,
    evidence_id: str,
    relation: str,  # "support" or "contradict"
) -> None:
    """Add an evidence link on a claim.

    Preconditions:
        claim_id and evidence_id exist and belong to the same case.
        relation in {"support", "contradict"}.
    Postconditions:
        Either supporting_evidence_ids or contradicting_evidence_ids contains
        evidence_id (idempotent — no duplicate entries).
    Raises:
        ClaimNotFoundError / EvidenceNotFoundError.
        ValueError on bad relation or cross-case link.
    Side effects:
        UPDATE claims (1 row).
        _emit_event("claim_status_changed", {claim_id, old_status: "linked",
        new_status: relation}).
    """
    if relation not in ("support", "contradict"):
        raise ValueError(f"Invalid relation: {relation}")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot link claim: no DB pool")

    async with pool.acquire() as conn:
        claim_row = await conn.fetchrow(
            "SELECT * FROM claims WHERE claim_id = $1", claim_id
        )
        ev_row = await conn.fetchrow(
            "SELECT case_id FROM evidence WHERE evidence_id = $1", evidence_id
        )

    if not claim_row:
        raise ClaimNotFoundError(f"Claim not found: {claim_id}")
    if not ev_row:
        raise EvidenceNotFoundError(f"Evidence not found: {evidence_id}")
    if claim_row["case_id"] != ev_row["case_id"]:
        raise ValueError("Cannot link evidence across cases")

    column = (
        "supporting_evidence_ids" if relation == "support" else "contradicting_evidence_ids"
    )
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            UPDATE claims
            SET {column} = (
                SELECT ARRAY(
                    SELECT DISTINCT unnest({column} || $1::text[])
                )
            ), updated_at = $2
            WHERE claim_id = $3
            """,
            [evidence_id],
            datetime.now(timezone.utc),
            claim_id,
        )

    await _emit_event(
        claim_row["case_id"],
        "claim_status_changed",
        {
            "claim_id": claim_id,
            "old_status": "linked",
            "new_status": relation,
        },
    )


# ---------------------------------------------------------------------------
# §4.8 Read functions
# ---------------------------------------------------------------------------

async def get_claim(claim_id: str) -> Claim:
    """Load a claim by ID. Raises ClaimNotFoundError."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot load claim: no DB pool")
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM claims WHERE claim_id = $1", claim_id)
    if row is None:
        raise ClaimNotFoundError(f"Claim not found: {claim_id}")
    return _row_to_claim(row)


async def get_claims_for_case(case_id: str) -> list[Claim]:
    """Return all claims for a case ordered by created_at ASC."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot list claims: no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM claims WHERE case_id = $1 ORDER BY created_at ASC",
            case_id,
        )
    return [_row_to_claim(row) for row in rows]


async def set_claim_status(
    claim_id: str,
    new_status: str,
    reason: Optional[str] = None,
) -> Claim:
    """Transition a claim's lifecycle status (audit gap #7).

    Gives the spec's ``claim_status_changed`` event type a real subject:
    the claims table now carries a status column
    ('ACTIVE' | 'SUPERSEDED' | 'RETRACTED') and this function is the
    sanctioned way to change it.

    Preconditions: claim_id exists; new_status is one of the three values.
    Postconditions:
        claims.status = new_status; updated_at bumped; if the new status is
        not ACTIVE, the claim's confidence is zeroed (a RETRACTED /
        SUPERSEDED claim must not keep supporting hypotheses).
        claim_status_changed event emitted with {claim_id, old_status,
        new_status, reason}.
    Raises:
        ClaimNotFoundError if the claim is missing.
        ValueError on an invalid status value or a no-op transition.
        DatabaseUnavailableError.
    Side effects:
        UPDATE claims (1 row).
        _emit_event("claim_status_changed", {...}).
    """
    from case_engine.schemas import ClaimStatus  # noqa: F401  (type ref)

    valid = {"ACTIVE", "SUPERSEDED", "RETRACTED"}
    if new_status not in valid:
        raise ValueError(f"new_status must be one of {sorted(valid)}")
    if not reason or not reason.strip():
        raise ValueError("reason must be non-empty")

    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot set claim status: no DB pool")

    async with pool.acquire() as conn:
        current = await conn.fetchrow(
            "SELECT status FROM claims WHERE claim_id = $1",
            claim_id,
        )
        if current is None:
            raise ClaimNotFoundError(claim_id)
        old_status = current["status"]
        if old_status == new_status:
            raise ValueError(
                f"claim already has status '{new_status}' (no-op transition)"
            )
        row = await conn.fetchrow(
            """
            UPDATE claims
            SET status = $2,
                confidence = CASE WHEN $2 = 'ACTIVE' THEN confidence ELSE 0.0 END,
                updated_at = NOW()
            WHERE claim_id = $1
            RETURNING *
            """,
            claim_id,
            new_status,
        )
    if row is None:
        raise ClaimNotFoundError(claim_id)

    _emit_event(
        "claim_status_changed",
        {
            "claim_id": claim_id,
            "case_id": row["case_id"],
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason.strip(),
        },
    )
    return _row_to_claim(row)


async def get_claims_for_hypothesis(hypothesis_id: str) -> list[Claim]:
    """Return all claims whose supporting_evidence_ids contribute to a hypothesis.

    Uses the evidence_graph_edges table: claims linked to the hypothesis via
    SUPPORTS or CONTRADICTS edges.
    """
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot list claims: no DB pool")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.* FROM claims c
            JOIN evidence_graph_edges e ON e.from_node = c.claim_id
            WHERE e.to_node = $1
              AND e.from_type = 'claim'
              AND e.relation IN ('SUPPORTS', 'CONTRADICTS')
            """,
            hypothesis_id,
        )
    return [_row_to_claim(row) for row in rows]


# ---------------------------------------------------------------------------
# §4.9 Contradiction candidates (word-overlap prefilter; audit gap #25)
# ---------------------------------------------------------------------------

_STOPWORDS = {"the", "of", "a", "an", "in", "on", "for", "to", "is", "was"}
_CANDIDATE_MIN_OVERLAP = 0.15
_CANDIDATE_LIMIT = 20


def _tokenize_statement(statement: str) -> set[str]:
    """Tokenize a statement into a lowercase alphanumeric word set (sync)."""
    words = re.findall(r"[a-z0-9]+", statement.lower())
    return {w for w in words if w not in _STOPWORDS}


def _statement_overlap(a: set[str], b: set[str]) -> float:
    """Jaccard word overlap between two token sets (sync)."""
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


async def find_contradicting_claims(
    claim_id: str,
    threshold: float = 0.7,
) -> list[Claim]:
    """Return claims in the same case that are CONTRADICTION CANDIDATES for the
    given claim.

    This is a candidate generator only — it does NOT decide contradictions.
    The real semantic check is case_engine.contradiction.detect_contradiction
    (Part 3), which the caller must run on every returned candidate.
    (Audit gap #25: the original spec returned every other claim in the case,
    which made detect_contradiction O(N^2); this prefilter keeps the candidate
    set small and cheap.)

    Prefilter: tokenize statements into lowercase alphanumeric word sets
    (dropping stopwords the/of/a/an/in/on/for/to/is/was), keep candidates whose
    Jaccard word overlap with the target is >= 0.15, capped at 20 candidates,
    ordered by created_at DESC.

    Preconditions: claim_id exists.
    Postconditions:
        Returns zero or more Claim objects.
        Never includes the input claim.
        Every returned claim is a candidate for detect_contradiction.
    Raises:
        ClaimNotFoundError.
        DatabaseUnavailableError.
    Side effects: none.
    Note: `threshold` is retained for signature compatibility with the spec
    (§4.9); it is reserved for the downstream detect_contradiction confidence
    and is not used by the word-overlap prefilter.
    """
    target = await get_claim(claim_id)
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot search claims: no DB pool")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM claims
            WHERE case_id = $1 AND claim_id <> $2
            ORDER BY created_at DESC
            LIMIT 50
            """,
            target.case_id,
            claim_id,
        )

    target_tokens = _tokenize_statement(target.statement)
    candidates: list[Claim] = []
    for row in rows:
        candidate = _row_to_claim(row)
        overlap = _statement_overlap(target_tokens, _tokenize_statement(candidate.statement))
        if overlap >= _CANDIDATE_MIN_OVERLAP:
            candidates.append(candidate)
        if len(candidates) >= _CANDIDATE_LIMIT:
            break
    return candidates