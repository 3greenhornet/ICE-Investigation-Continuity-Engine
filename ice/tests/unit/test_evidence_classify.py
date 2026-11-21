"""Unit tests for evidence type classification (case_engine/evidence.py).

No database, no LLM. Covers ``classify_evidence_type`` — the ingestion
boundary's pattern-based classifier — against the EVIDENCE_TYPE_PATTERNS
table and its OBSERVATION fallback.

Engine surface under test (case_engine/evidence.py):
    classify_evidence_type(content) -> EvidenceType
        Case-insensitive regex match, evaluated in EVIDENCE_TYPE_PATTERNS
        dict order (MEASUREMENT first — a sentence matching both a
        measurement and an observation pattern classifies as
        MEASUREMENT); empty input -> "OBSERVATION".

Doc-outline adaptations (verified against the pattern table):
    * "percent -> MEASUREMENT": our pattern is r"\\d+(\\.\\d+)?\\s*(ms|s|kb|mb|gb|%|rpm|qps|iops)"
      — "error rate 3%" matches via the % unit.
    * "first-person -> OBSERVATION": pattern is (i|we|they) (saw|observed|noticed|found)
      or (appears|seems|looks like).
    * "engineer said -> HUMAN_ASSERTION": pattern is "the (engineer|user|participant) said".
    * "git command -> TOOL_RESULT": pattern anchors at line start (^git|kubectl|docker|psql|curl) —
      "git log --oneline" qualifies.
    * "suggests -> MODEL_INFERENCE": pattern is ^("this|that") suggests — the doc's
      bare "suggests" must be phrased "This suggests ..." to hit the pattern.
"""
import pytest

from case_engine.evidence import classify_evidence_type


class TestClassifyEvidenceType:
    """classify_evidence_type: pattern table + fallback."""

    @pytest.mark.unit
    def test_classify_measurement_latency(self):
        assert classify_evidence_type("p99 latency was 250ms after the deploy") == "MEASUREMENT"

    @pytest.mark.unit
    def test_classify_measurement_percent(self):
        assert classify_evidence_type("error rate was 3% during the incident") == "MEASUREMENT"

    @pytest.mark.unit
    def test_classify_observation_first_person(self):
        assert classify_evidence_type("I saw the checkout service fail twice") == "OBSERVATION"

    @pytest.mark.unit
    def test_classify_document_claim_according_to(self):
        assert classify_evidence_type("According to the runbook, the queue drains hourly") == "DOCUMENT_CLAIM"

    @pytest.mark.unit
    def test_classify_human_assertion_engineer_said(self):
        assert classify_evidence_type("the engineer said the pool was exhausted") == "HUMAN_ASSERTION"

    @pytest.mark.unit
    def test_classify_tool_result_git_command(self):
        assert classify_evidence_type("git log --oneline -5") == "TOOL_RESULT"

    @pytest.mark.unit
    def test_classify_model_inference_suggests(self):
        assert classify_evidence_type("This suggests a memory leak in the cache layer") == "MODEL_INFERENCE"

    @pytest.mark.unit
    def test_classify_empty_returns_observation(self):
        assert classify_evidence_type("") == "OBSERVATION"

    # --- beyond the doc outline: table coverage + precedence ----------

    @pytest.mark.unit
    def test_classify_model_summary(self):
        assert classify_evidence_type("In summary, the incident had two causes") == "MODEL_SUMMARY"

    @pytest.mark.unit
    def test_classify_experiment_result(self):
        assert classify_evidence_type("we ran a load test and it failed") == "EXPERIMENT_RESULT"

    @pytest.mark.unit
    def test_classify_no_match_returns_observation(self):
        # Plain declarative sentence, no pattern hit -> fallback
        assert classify_evidence_type("the database is in region three") == "OBSERVATION"

    @pytest.mark.unit
    def test_classify_measurement_wins_over_observation(self):
        # Both patterns match; MEASUREMENT is earlier in the table
        content = "I saw p99 latency of 250ms"
        assert classify_evidence_type(content) == "MEASUREMENT"

    @pytest.mark.unit
    def test_classify_is_case_insensitive(self):
        assert classify_evidence_type("ACCORDING TO THE DOCS IT RESTARTS") == "DOCUMENT_CLAIM"

    @pytest.mark.unit
    def test_classify_tool_result_other_commands(self):
        # pattern is ^(cmd)\s+\w+ — the sub-command must be a word
        assert classify_evidence_type("kubectl get pods") == "TOOL_RESULT"
        assert classify_evidence_type("docker ps") == "TOOL_RESULT"
        assert classify_evidence_type("curl https://api.example.com/health") == "TOOL_RESULT"

    @pytest.mark.unit
    def test_classify_measurement_keyed_number(self):
        # "latency: 250" hits the (latency|...)\s*[:=]\s*\d+ pattern
        assert classify_evidence_type("latency: 250") == "MEASUREMENT"
        assert classify_evidence_type("cpu=92") == "MEASUREMENT"

    @pytest.mark.unit
    def test_classify_whitespaced_unit_still_measurement(self):
        # \d+\s*(ms|...) tolerates a space between number and unit
        assert classify_evidence_type("query took 250 ms") == "MEASUREMENT"