"""Unit tests for claim statement helpers (case_engine/claim.py).

No database, no LLM. Covers the word-overlap prefilter that feeds
``find_contradicting_claims`` (audit gap #25): tokenization with stopword
removal and Jaccard overlap.

Doc-outline adaptation (verified against the implemented claim.py):
    The doc's outline lists a "normalize" trio (lowercase / punctuation /
    whitespace). claim.py has NO _normalize_statement — normalization
    lives in hypothesis.py (see test_hypothesis_normalize.py) while the
    claim plane normalizes *through* _tokenize_statement (lowercase +
    alphanumeric-only + stopword removal). Those normalization properties
    are therefore asserted via the tokenizer, which is the actual claim-
    plane surface.
"""
import pytest

from case_engine.claim import _tokenize_statement, _statement_overlap, _STOPWORDS


class TestTokenizeStatement:
    """_tokenize_statement: lowercase alphanumeric words minus stopwords."""

    @pytest.mark.unit
    def test_tokenize_lowercases(self):
        assert _tokenize_statement("Latency ROSE Sharply") == {"latency", "rose", "sharply"}

    @pytest.mark.unit
    def test_tokenize_strips_punctuation(self):
        # Punctuation splits tokens; only alphanumeric runs survive
        assert _tokenize_statement("error-rate, latency!") == {"error", "rate", "latency"}

    @pytest.mark.unit
    def test_tokenize_collapses_whitespace(self):
        assert _tokenize_statement("cpu  is\nhigh") == {"cpu", "high"}

    @pytest.mark.unit
    def test_tokenize_removes_stopwords(self):
        tokens = _tokenize_statement("the cache of a pool in the region")
        assert tokens == {"cache", "pool", "region"}
        # every engine stopword is really excluded
        assert not (tokens & _STOPWORDS)

    @pytest.mark.unit
    def test_tokenize_empty_returns_empty_set(self):
        assert _tokenize_statement("") == set()
        assert _tokenize_statement("   ") == set()

    @pytest.mark.unit
    def test_tokenize_punctuation_only_returns_empty_set(self):
        assert _tokenize_statement("!!! ??? ...") == set()

    @pytest.mark.unit
    def test_tokenize_keeps_digits(self):
        # digits survive; "was" is a stopword and is dropped
        assert _tokenize_statement("p99 was 250ms") == {"p99", "250ms"}


class TestStatementOverlap:
    """_statement_overlap: Jaccard similarity between token sets."""

    @pytest.mark.unit
    def test_overlap_identical_is_one(self):
        a = _tokenize_statement("latency rose after the deploy")
        b = _tokenize_statement("latency rose after the deploy")
        assert _statement_overlap(a, b) == 1.0

    @pytest.mark.unit
    def test_overlap_disjoint_is_zero(self):
        a = _tokenize_statement("latency rose")
        b = _tokenize_statement("memory leaked")
        assert _statement_overlap(a, b) == 0.0

    @pytest.mark.unit
    def test_statement_overlap_jaccard(self):
        # a={latency, rose}, b={latency, fell}: intersection 1, union 3
        a = _tokenize_statement("latency rose")
        b = _tokenize_statement("latency fell")
        assert _statement_overlap(a, b) == pytest.approx(1 / 3)

    @pytest.mark.unit
    def test_overlap_half_shared_is_half(self):
        # {a, b} vs {a, b, c, d}: intersection 2, union 4 -> 0.5
        assert _statement_overlap({"a", "b"}, {"a", "b", "c", "d"}) == 0.5

    @pytest.mark.unit
    def test_overlap_both_empty_is_zero(self):
        assert _statement_overlap(set(), set()) == 0.0

    @pytest.mark.unit
    def test_overlap_one_empty_is_zero(self):
        assert _statement_overlap({"latency"}, set()) == 0.0

    @pytest.mark.unit
    def test_overlap_is_symmetric(self):
        a = _tokenize_statement("the pool was exhausted")
        b = _tokenize_statement("pool exhausted connections")
        assert _statement_overlap(a, b) == pytest.approx(_statement_overlap(b, a))

    @pytest.mark.unit
    def test_overlap_punctuation_and_case_insensitive_end_to_end(self):
        # The doc's "normalize" properties, asserted through the tokenizer:
        # case and punctuation cannot change the overlap score.
        a = _tokenize_statement("Latency rose!")
        b = _tokenize_statement("latency rose")
        assert _statement_overlap(a, b) == 1.0

    @pytest.mark.unit
    def test_overlap_meets_candidate_threshold_for_contradiction_pair(self):
        # The prefilter keeps candidates with overlap >= 0.15
        # (_CANDIDATE_MIN_OVERLAP): "latency rose" vs "latency rose after
        # deploy" = 2/4 = 0.5 — well above the bar a real contradiction
        # candidate needs.
        from case_engine.claim import _CANDIDATE_MIN_OVERLAP

        a = _tokenize_statement("latency rose")
        b = _tokenize_statement("latency rose after deploy")
        assert _statement_overlap(a, b) >= _CANDIDATE_MIN_OVERLAP