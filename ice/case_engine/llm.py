"""case_engine/llm.py — single source of truth for every LLM call in ICE.

Owner: ICE foundation (spec Part 2 §1; circuit breaker spec Part 4 §20.1).
Purpose: structured JSON extraction with a strict provider fallback chain,
    robust JSON parsing, deterministic logging, a circuit breaker against
    provider outages, and optional deterministic seeding for benchmarks.
    Per mandatory convention §3.3.4 every LLM call in the engine goes through
    ``_call_llm_json`` (or ``_call_llm_text`` for plain prose).
Allowed downstream dependencies: litellm, stdlib, and — lazily, inside the
    call functions — ``case_engine.exceptions.LLMUnavailableError``.

Audit-mandated wiring (worklog ice-0):
  * gap #18: the ``_CircuitBreaker`` from spec §20.1 is now actually wired
    into ``_call_llm_json`` and ``_call_llm_text`` (check ``is_open()`` at
    the top; ``record_success()`` on a usable response; ``record_failure()``
    on any failed attempt — provider exception, unparseable JSON, or empty
    text).
  * improvisation I: ``_maybe_seed(kwargs)`` attaches ``seed=...`` when
    EXPERIMENT_SEED is set and the provider supports it (groq/*,
    openrouter/*), for benchmark reproducibility.

Hari ICE "addition and improvements" pass additions (see CHANGES.md):
  * ROLE-BASED MODEL ROUTING (second-pass audit: "provider fallback is
    failure recovery, not independent reasoning"). Roles GENERATOR / CRITIC
    / RANKER / EXTRACTOR can each get a dedicated model chain via the
    LLM_ROLE_<ROLE> env vars (comma-separated model list). Role chains are
    tried BEFORE the global fallback chain, so different models can
    generate, critique and rank explanations — cognitive diversity instead
    of one model reinforcing its own mistake. Falls back gracefully when a
    role has no dedicated models or their keys are missing.
  * LLM LEDGER (migration 020): every attempt is recorded with operation,
    role, prompt_id/prompt_version, model, request/response hashes, token
    estimates (usage metadata when the provider returns it, else a
    deterministic chars/4 estimate), latency and success — the
    prompt-reproducibility and cost-measurement gaps from the audits.
    ``get_llm_usage(case_id=...)`` aggregates tokens per operation/role.
"""

import hashlib
import json
import logging
import os
import re
import time
import uuid
import zlib
from typing import Optional

import litellm
from litellm import acompletion

litellm.drop_params = True
litellm.num_retries = 2

logger = logging.getLogger(__name__)

DEFAULT_FALLBACK_MODELS: list[str] = [
    "groq/openai/gpt-oss-120b",
    "groq/openai/gpt-oss-20b",
    "openrouter/meta-llama/llama-3.3-70b-instruct",
    "gemini/gemini-2.5-flash",
    "mistral/mistral-small-latest",
]

_PROVIDER_KEY_MAP: dict[str, str] = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}

LLM_TIMEOUT = float(os.getenv("LITELLM_NETWORK_TIMEOUT", "15.0"))

# Roles for cognitive diversity (GENERATOR proposes, CRITIC attacks,
# RANKER compares, EXTRACTOR does structured extraction, GENERAL is the
# default when no role is given).
LLM_ROLES = ("GENERATOR", "CRITIC", "RANKER", "EXTRACTOR", "GENERAL")

# Ledger writing is best-effort: a broken ledger must never break an LLM
# call. Also allows tests to disable it entirely.
_LEDGER_ENABLED = os.getenv("ENABLE_LLM_LEDGER", "true").lower() == "true"


def _resolve_active_models() -> list[str]:
    """Return the ordered list of fallback models whose provider API key is set.

    Preconditions:
        None (reads environment only).
    Postconditions:
        Order preserves LLM_FALLBACK_CHAIN (or DEFAULT_FALLBACK_MODELS).
        Models whose provider key is missing or empty are dropped.
    Raises:
        Nothing.
    Side effects:
        Logs one warning per skipped model.
    """
    chain = os.getenv("LLM_FALLBACK_CHAIN", "").strip()
    models = (
        [m.strip() for m in chain.split(",") if m.strip()]
        if chain
        else DEFAULT_FALLBACK_MODELS
    )
    return _filter_keyed(models)


def _filter_keyed(models: list[str]) -> list[str]:
    """Drop models whose provider key is missing; warn once each."""
    active: list[str] = []
    for model in models:
        provider = model.split("/", 1)[0].lower()
        key_env = _PROVIDER_KEY_MAP.get(provider)
        if key_env is None:
            logger.warning(
                "Unknown provider '%s' in model '%s'; skipping", provider, model
            )
            continue
        if not os.getenv(key_env, "").strip():
            logger.warning(
                "Provider key %s missing; skipping model %s", key_env, model
            )
            continue
        active.append(model)
    return active


def _role_models(role: str) -> list[str]:
    """Resolve the dedicated model chain for a cognitive role.

    Reads LLM_ROLE_<ROLE> (comma-separated model list) at CALL time.
    Returns [] when the role has no dedicated chain — the caller then falls
    back to the global ACTIVE_FALLBACK_MODELS chain.
    """
    role = (role or "GENERAL").upper()
    if role not in LLM_ROLES:
        role = "GENERAL"
    raw = os.getenv(f"LLM_ROLE_{role}", "").strip()
    if not raw:
        return []
    return _filter_keyed([m.strip() for m in raw.split(",") if m.strip()])


ACTIVE_FALLBACK_MODELS: list[str] = _resolve_active_models()


def llm_available() -> bool:
    """True when at least one provider model is configured with a key.

    Deterministic engine paths (induction fallback, retrieval, benchmark
    offline mode) branch on this instead of catching LLMUnavailableError.
    """
    return bool(ACTIVE_FALLBACK_MODELS)


# =====================================================================
# LLM ledger (migration 020) — prompt/operation reproducibility + cost
# =====================================================================


def _estimate_tokens(text: str) -> int:
    """Deterministic token estimate (~chars/4) when usage metadata is absent."""
    return max(0, len(text or "") // 4)


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()[:16]


async def record_llm_call(
    operation: str,
    role: str,
    model: str,
    prompt_id: str,
    prompt_version: str,
    prompt_text: str,
    response_text: Optional[str],
    latency_ms: float,
    success: bool,
    error: Optional[str] = None,
    case_id: Optional[str] = None,
    usage: Optional[dict] = None,
) -> None:
    """Best-effort ledger write for one LLM operation (never raises)."""
    if not _LEDGER_ENABLED:
        return
    try:
        from db.connection import get_pool

        pool = await get_pool()
        if pool is None:
            return
        usage = usage or {}
        tokens_in = usage.get("prompt_tokens")
        tokens_out = usage.get("completion_tokens")
        if tokens_in is None:
            tokens_in = _estimate_tokens(prompt_text)
        if tokens_out is None:
            tokens_out = _estimate_tokens(response_text or "")
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_ledger (
                    ledger_id, case_id, operation, role, prompt_id,
                    prompt_version, model, request_hash, response_hash,
                    tokens_in, tokens_out, latency_ms, success, error
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14
                )
                """,
                f"led_{uuid.uuid4().hex[:12]}",
                case_id,
                operation,
                (role or "GENERAL").upper(),
                prompt_id,
                prompt_version,
                model,
                _hash_text(prompt_text),
                _hash_text(response_text or ""),
                int(tokens_in),
                int(tokens_out),
                float(latency_ms),
                bool(success),
                error,
            )
    except Exception as exc:  # never break the caller
        logger.debug("llm_ledger write failed: %s", exc)


async def get_llm_usage(case_id: Optional[str] = None) -> dict:
    """Aggregate token usage per operation/role from the ledger.

    Returns {"total_calls", "total_tokens_in", "total_tokens_out",
    "by_operation": {op: {calls, tokens_in, tokens_out}},
    "by_role": {role: {calls, tokens}}}. Empty structure when the ledger
    table is unavailable (e.g. migration 020 not applied).
    """
    try:
        from db.connection import get_pool

        pool = await get_pool()
        if pool is None:
            return {"total_calls": 0, "total_tokens_in": 0, "total_tokens_out": 0}
        async with pool.acquire() as conn:
            if case_id:
                rows = await conn.fetch(
                    """
                    SELECT operation, role,
                           COUNT(*) AS calls,
                           COALESCE(SUM(tokens_in), 0) AS tin,
                           COALESCE(SUM(tokens_out), 0) AS tout
                    FROM llm_ledger WHERE case_id = $1
                    GROUP BY operation, role
                    """,
                    case_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT operation, role,
                           COUNT(*) AS calls,
                           COALESCE(SUM(tokens_in), 0) AS tin,
                           COALESCE(SUM(tokens_out), 0) AS tout
                    FROM llm_ledger
                    GROUP BY operation, role
                    """
                )
        by_op: dict[str, dict] = {}
        by_role: dict[str, dict] = {}
        total_in = total_out = total_calls = 0
        for r in rows:
            calls, tin, tout = int(r["calls"]), int(r["tin"]), int(r["tout"])
            total_calls += calls
            total_in += tin
            total_out += tout
            op = by_op.setdefault(
                r["operation"], {"calls": 0, "tokens_in": 0, "tokens_out": 0}
            )
            op["calls"] += calls
            op["tokens_in"] += tin
            op["tokens_out"] += tout
            rl = by_role.setdefault(
                r["role"], {"calls": 0, "tokens_in": 0, "tokens_out": 0}
            )
            rl["calls"] += calls
            rl["tokens_in"] += tin
            rl["tokens_out"] += tout
        return {
            "total_calls": total_calls,
            "total_tokens_in": total_in,
            "total_tokens_out": total_out,
            "by_operation": by_op,
            "by_role": by_role,
        }
    except Exception as exc:
        logger.debug("llm usage aggregation failed: %s", exc)
        return {"total_calls": 0, "total_tokens_in": 0, "total_tokens_out": 0}


# =====================================================================
# Circuit breaker (spec Part 4 §20.1; wired here per audit gap #18)
# =====================================================================


class _CircuitBreaker:
    """Trip after ``threshold`` consecutive-ish failures; auto-close after
    ``recovery_seconds`` so callers can retry without a restart."""

    def __init__(self, threshold: int = 5, recovery_seconds: float = 60.0):
        self.threshold = threshold
        self.recovery_seconds = recovery_seconds
        self.failure_count = 0
        self.opened_at: Optional[float] = None

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.time() - self.opened_at > self.recovery_seconds:
            self.opened_at = None
            self.failure_count = 0
            return False
        return True

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.threshold:
            self.opened_at = time.time()

    def record_success(self) -> None:
        self.failure_count = 0
        self.opened_at = None


_breaker = _CircuitBreaker()


# =====================================================================
# Deterministic seeding (audit improvisation I)
# =====================================================================


def _maybe_seed(kwargs: dict) -> None:
    """Attach ``seed`` to an acompletion kwargs dict when reproducibility is
    requested and the provider supports it.

    Preconditions:
        kwargs contains a "model" key (str) and is mutable.
    Postconditions:
        When EXPERIMENT_SEED is non-empty and the model starts with
        "groq/" or "openrouter/" (Gemini/Mistral do not reliably honor
        ``seed``), kwargs["seed"] is a deterministic int derived from the
        env value. Otherwise kwargs is unchanged.
    Raises:
        Nothing.
    Side effects:
        None (beyond mutating kwargs as described).

    Note: the int is derived via zlib.crc32 (stable across processes), not
    the builtin hash(), which is salted per process and would silently break
    benchmark reproducibility.
    """
    seed_env = os.getenv("EXPERIMENT_SEED", "").strip()
    if not seed_env:
        return
    model = str(kwargs.get("model", ""))
    if not (model.startswith("groq/") or model.startswith("openrouter/")):
        return
    kwargs["seed"] = int(zlib.crc32(seed_env.encode("utf-8")))


# =====================================================================
# JSON extraction
# =====================================================================


def _extract_json_safely(raw_text: str) -> str:
    """Extract the first valid JSON object from a string.

    Preconditions:
        raw_text is a string (may be empty).
    Postconditions:
        Returns a compact JSON string that parses with json.loads().
    Raises:
        ValueError: if no valid JSON object is found.
    Side effects:
        None.
    """
    text = (raw_text or "").strip()
    decoder = json.JSONDecoder()

    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[start:])
            return json.dumps(obj, separators=(",", ":"))
        except json.JSONDecodeError:
            continue

    # Greedy fallback: outermost braces, even if raw_decode rejected it —
    # the caller's json.loads() will retry and route to the next model.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)

    raise ValueError("No valid JSON object found in the response")


# =====================================================================
# Public call paths
# =====================================================================


async def _call_llm_json(
    prompt: str,
    system_prompt: str = (
        "You are a structured data extraction engine. Return ONLY valid JSON."
    ),
    temperature: float = 0.1,
    timeout: float = LLM_TIMEOUT,
    max_retries: int = 3,
    role: str = "GENERAL",
    operation: str = "json_call",
    prompt_id: str = "adhoc",
    prompt_version: str = "v0",
    case_id: Optional[str] = None,
) -> Optional[dict]:
    """Call LLMs in the fallback chain until one returns parseable JSON.

    Role routing (Hari ICE improvements pass): the dedicated chain for
    ``role`` (LLM_ROLE_<ROLE> env, e.g. a different provider for the CRITIC)
    is tried BEFORE the global fallback chain — model diversity for
    generate/critique/rank instead of one model reinforcing itself.

    Ledger: exactly one ledger row is written per call attempt batch
    (success or terminal failure) with prompt_id/version, model, hashes,
    token estimates and latency (migration 020).

    Preconditions:
        The circuit breaker is closed (checked at call time).
        At least one model chain is non-empty.
        prompt is a non-empty string.
    Postconditions:
        Returns a Python dict parsed from the LLM's response.
        Returns None if every model × every retry fails.
    Raises:
        LLMUnavailableError: if the circuit breaker is open or every
            chain is empty.
        ValueError: if prompt is empty.
    Side effects:
        Breaker: record_success() on a parsed response; record_failure()
        on every failed attempt (provider exception, unparseable JSON, or
        a response containing no JSON object).
        One best-effort llm_ledger row (success flag set accordingly).
    """
    from case_engine.exceptions import LLMUnavailableError

    if _breaker.is_open():
        raise LLMUnavailableError("Circuit breaker open")

    model_chain = _role_models(role) or list(ACTIVE_FALLBACK_MODELS)
    if not model_chain:
        raise LLMUnavailableError(
            "No LLM providers configured (no role chain, "
            "ACTIVE_FALLBACK_MODELS empty)"
        )

    if not prompt or not prompt.strip():
        raise ValueError("_call_llm_json requires a non-empty prompt")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    started = time.time()
    last_error: Optional[str] = None

    for model in model_chain:
        for attempt in range(max_retries):
            try:
                kwargs: dict = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "timeout": timeout,
                }
                if not model.startswith("openrouter"):
                    kwargs["response_format"] = {"type": "json_object"}

                _maybe_seed(kwargs)

                response = await acompletion(**kwargs)
                raw = response.choices[0].message.content
                clean = _extract_json_safely(raw)
                parsed = json.loads(clean)
                _breaker.record_success()
                usage = getattr(response, "usage", None)
                usage_dict = (
                    {"prompt_tokens": usage.prompt_tokens,
                     "completion_tokens": usage.completion_tokens}
                    if usage else None
                )
                await record_llm_call(
                    operation=operation, role=role, model=model,
                    prompt_id=prompt_id, prompt_version=prompt_version,
                    prompt_text=prompt, response_text=raw,
                    latency_ms=(time.time() - started) * 1000.0,
                    success=True, case_id=case_id, usage=usage_dict,
                )
                return parsed

            except json.JSONDecodeError as exc:
                last_error = f"JSON decode failed: {exc}"
                _breaker.record_failure()
                logger.debug(
                    "attempt=%d model=%s parse_failed: %s", attempt, model, exc
                )
                continue

            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                _breaker.record_failure()
                logger.debug(
                    "attempt=%d model=%s call_failed: %s", attempt, model, exc
                )
                break  # Move to next model

    logger.error("All LLM models exhausted. Last error: %s", last_error)
    await record_llm_call(
        operation=operation, role=role, model=model_chain[0],
        prompt_id=prompt_id, prompt_version=prompt_version,
        prompt_text=prompt, response_text=None,
        latency_ms=(time.time() - started) * 1000.0,
        success=False, error=last_error, case_id=case_id,
    )
    return None


async def _call_llm_text(
    prompt: str,
    system_prompt: str = "",
    temperature: float = 0.3,
    timeout: float = LLM_TIMEOUT,
    role: str = "GENERAL",
    operation: str = "text_call",
    prompt_id: str = "adhoc",
    prompt_version: str = "v0",
    case_id: Optional[str] = None,
) -> Optional[str]:
    """Call LLMs in the fallback chain until one returns non-empty text.

    Role routing + ledger identical to _call_llm_json.

    Preconditions:
        The circuit breaker is closed (checked at call time).
        At least one model chain is non-empty.
        prompt is a non-empty string.
    Postconditions:
        Returns stripped response text.
        Returns None if every model fails or returns empty text.
    Raises:
        LLMUnavailableError: if the circuit breaker is open or every
            chain is empty.
        ValueError: if prompt is empty.
    Side effects:
        Breaker + one best-effort llm_ledger row (see _call_llm_json).
    """
    from case_engine.exceptions import LLMUnavailableError

    if _breaker.is_open():
        raise LLMUnavailableError("Circuit breaker open")

    model_chain = _role_models(role) or list(ACTIVE_FALLBACK_MODELS)
    if not model_chain:
        raise LLMUnavailableError("No LLM providers configured")

    if not prompt or not prompt.strip():
        raise ValueError("_call_llm_text requires a non-empty prompt")

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    started = time.time()
    for model in model_chain:
        try:
            kwargs: dict = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "timeout": timeout,
            }
            _maybe_seed(kwargs)

            response = await acompletion(**kwargs)
            text = (response.choices[0].message.content or "").strip()
            if text:
                _breaker.record_success()
                usage = getattr(response, "usage", None)
                usage_dict = (
                    {"prompt_tokens": usage.prompt_tokens,
                     "completion_tokens": usage.completion_tokens}
                    if usage else None
                )
                await record_llm_call(
                    operation=operation, role=role, model=model,
                    prompt_id=prompt_id, prompt_version=prompt_version,
                    prompt_text=prompt, response_text=text,
                    latency_ms=(time.time() - started) * 1000.0,
                    success=True, case_id=case_id, usage=usage_dict,
                )
                return text
            _breaker.record_failure()
            logger.debug("model=%s returned empty text; trying next model", model)
        except Exception as exc:
            _breaker.record_failure()
            logger.debug("model=%s text_call_failed: %s", model, exc)
            continue

    await record_llm_call(
        operation=operation, role=role, model=model_chain[0],
        prompt_id=prompt_id, prompt_version=prompt_version,
        prompt_text=prompt, response_text=None,
        latency_ms=(time.time() - started) * 1000.0,
        success=False, error="all models exhausted", case_id=case_id,
    )
    return None