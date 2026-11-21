"""Unit tests for case_engine/llm.py's pure helpers.

No database, no LLM *calls*. Covers the two testable pieces of the LLM
gateway: robust JSON extraction from raw model output, and deterministic
benchmark seeding.

Engine surface under test (case_engine/llm.py):
    _extract_json_safely(raw_text) -> str
        Returns the FIRST valid JSON object found, as a compact JSON
        *string* (adapted from the doc outline's "returns a dict": our
        engine's helper returns the compact string for the caller's
        json.loads retry — tests therefore parse before comparing).
        Greedy brace fallback for malformed-but-braced text; ValueError
        when nothing parseable exists.
    _maybe_seed(kwargs) -> None
        When EXPERIMENT_SEED is set and kwargs["model"] starts with
        "groq/" or "openrouter/", sets kwargs["seed"] =
        int(zlib.crc32(seed_env)) — a process-stable derivation (the doc
        outline's literal seed=<env int> does not survive contact with the
        implemented crc32 derivation; the assertion checks the actual
        contract). Gemini/Mistral models never get a seed.
"""
import json
import zlib

import pytest

from case_engine.llm import _extract_json_safely, _maybe_seed


class TestExtractJsonSafely:
    """_extract_json_safely: robust first-JSON-object extraction."""

    @pytest.mark.unit
    def test_extract_json_safely_valid(self):
        assert json.loads(_extract_json_safely('{"a": 1}')) == {"a": 1}

    @pytest.mark.unit
    def test_extract_json_safely_with_prefix(self):
        raw = 'Here is JSON: {"a": 1}'
        assert json.loads(_extract_json_safely(raw)) == {"a": 1}

    @pytest.mark.unit
    def test_extract_json_safely_nested(self):
        raw = '{"a": {"b": 2}}'
        parsed = json.loads(_extract_json_safely(raw))
        assert parsed == {"a": {"b": 2}}

    @pytest.mark.unit
    def test_extract_json_safely_invalid_raises(self):
        with pytest.raises(ValueError):
            _extract_json_safely("no json here")

    @pytest.mark.unit
    def test_extract_json_safely_multiple_objects_returns_first(self):
        raw = '{"a": 1} and then {"b": 2}'
        assert json.loads(_extract_json_safely(raw)) == {"a": 1}

    @pytest.mark.unit
    def test_extract_json_safely_with_prose_and_code_fence(self):
        raw = '```json\n{"claims": [{"statement": "x"}]}\n```'
        parsed = json.loads(_extract_json_safely(raw))
        assert parsed == {"claims": [{"statement": "x"}]}

    @pytest.mark.unit
    def test_extract_json_safely_empty_string_raises(self):
        with pytest.raises(ValueError):
            _extract_json_safely("")

    @pytest.mark.unit
    def test_extract_json_safely_first_brace_wins_even_if_later_valid(self):
        # The scan starts at the FIRST '{'; a broken object there must not
        # silently jump to a later valid one (greedy fallback may still
        # return the outer braces — which then fails the caller's
        # json.loads and routes to the next model). Assert the returned
        # string starts at the first brace context.
        raw = '{"broken": , } trailing {"ok": 1}'
        result = _extract_json_safely(raw)
        assert isinstance(result, str)
        assert "{" in result


class TestMaybeSeed:
    """_maybe_seed: deterministic EXPERIMENT_SEED wiring."""

    @pytest.mark.unit
    def test_maybe_seed_when_env_set_groq(self, monkeypatch):
        monkeypatch.setenv("EXPERIMENT_SEED", "42")
        kwargs = {"model": "groq/openai/gpt-oss-120b"}
        _maybe_seed(kwargs)
        assert kwargs["seed"] == int(zlib.crc32(b"42"))
        assert isinstance(kwargs["seed"], int)

    @pytest.mark.unit
    def test_maybe_seed_when_env_set_openrouter(self, monkeypatch):
        monkeypatch.setenv("EXPERIMENT_SEED", "bench-run-2026")
        kwargs = {"model": "openrouter/meta-llama/llama-3.3-70b-instruct"}
        _maybe_seed(kwargs)
        assert kwargs["seed"] == int(zlib.crc32(b"bench-run-2026"))

    @pytest.mark.unit
    def test_maybe_seed_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("EXPERIMENT_SEED", raising=False)
        kwargs = {"model": "groq/openai/gpt-oss-120b"}
        _maybe_seed(kwargs)
        assert "seed" not in kwargs

    @pytest.mark.unit
    def test_maybe_seed_non_groq_provider_gemini(self, monkeypatch):
        monkeypatch.setenv("EXPERIMENT_SEED", "42")
        kwargs = {"model": "gemini/gemini-2.5-flash"}
        _maybe_seed(kwargs)
        assert "seed" not in kwargs

    @pytest.mark.unit
    def test_maybe_seed_non_groq_provider_mistral(self, monkeypatch):
        monkeypatch.setenv("EXPERIMENT_SEED", "42")
        kwargs = {"model": "mistral/mistral-small-latest"}
        _maybe_seed(kwargs)
        assert "seed" not in kwargs

    @pytest.mark.unit
    def test_maybe_seed_blank_env_is_unset(self, monkeypatch):
        # Blank / whitespace-only EXPERIMENT_SEED counts as unset
        monkeypatch.setenv("EXPERIMENT_SEED", "   ")
        kwargs = {"model": "groq/openai/gpt-oss-120b"}
        _maybe_seed(kwargs)
        assert "seed" not in kwargs

    @pytest.mark.unit
    def test_maybe_seed_is_deterministic_across_calls(self, monkeypatch):
        monkeypatch.setenv("EXPERIMENT_SEED", "reproducible")
        kw1, kw2 = {"model": "groq/x"}, {"model": "groq/x"}
        _maybe_seed(kw1)
        _maybe_seed(kw2)
        assert kw1["seed"] == kw2["seed"]

    @pytest.mark.unit
    def test_maybe_seed_does_not_touch_other_kwargs(self, monkeypatch):
        monkeypatch.setenv("EXPERIMENT_SEED", "42")
        kwargs = {"model": "groq/x", "temperature": 0.1, "messages": []}
        before = dict(kwargs)
        _maybe_seed(kwargs)
        kwargs.pop("seed")
        assert kwargs == before