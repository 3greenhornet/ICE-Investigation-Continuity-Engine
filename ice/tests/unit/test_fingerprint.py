"""Domain-stripped fingerprints (case_engine.fingerprint). LLM mocked."""
import pytest

pytestmark = pytest.mark.unit

ECOLOGY = {"title": "Patch leaving in foragers", "url": "u-eco", "field": "Ecology",
           "abstract": "Animals decide when to leave a depleting food patch..."}
CHATBOT = {"title": "Chatbot personas", "url": "u-bot", "field": "AI",
           "abstract": "A chatbot that talks to users about user interests..."}


def test_rank_prefers_structural_twin_over_vocabulary_overlap():
    from case_engine.fingerprint import _key, rank_works

    problem_fp = {"MECHANISM": "choose items from a ranked pool balancing estimated value against "
                               "redundancy with already chosen items and occasionally explore unseen items",
                  "STRUCTURE": "other: sequential selection", "DATA_OBJECT": "set or table",
                  "INFERENCE": "bayesian posterior", "PROBLEM_FORM": "ranking or retrieval"}
    fps = {
        _key(ECOLOGY): {"MECHANISM": "sequentially choose items from a pool, balancing estimated value "
                                     "of each item against redundancy, explore unseen items with posterior "
                                     "sampling", "STRUCTURE": "other: sequential selection",
                        "DATA_OBJECT": "set or table", "INFERENCE": "bayesian posterior",
                        "PROBLEM_FORM": "ranking or retrieval"},
        _key(CHATBOT): {"MECHANISM": "fine-tune a sequence model on dialogue transcripts",
                        "STRUCTURE": "dense linear algebra", "DATA_OBJECT": "sequence or time-series",
                        "INFERENCE": "optimization only", "PROBLEM_FORM": "simulation or generation"},
    }
    ranked = rank_works(problem_fp, "chatbot talks about user interests", [CHATBOT, ECOLOGY], fps)
    assert ranked[0]["url"] == "u-eco"
    assert set(ranked[0]["facet_matches"]) == {"STRUCTURE", "DATA_OBJECT", "INFERENCE", "PROBLEM_FORM"}


def test_rank_without_fingerprints_falls_back_to_abstracts():
    from case_engine.fingerprint import rank_works

    ranked = rank_works(None, "depleting food patch leave decision", [CHATBOT, ECOLOGY], {})
    assert ranked[0]["url"] == "u-eco" and ranked[0]["facet_matches"] == []


@pytest.mark.asyncio
async def test_fingerprint_works_batches_maps_ids_and_caches(monkeypatch, tmp_path):
    import case_engine.fingerprint as fp

    calls = []

    async def fake(prompt, op, case_id):
        calls.append(op)
        return {"fingerprints": [
            {"id": "P2", "MECHANISM": "second", "STRUCTURE": "Graph Traversal."},
            {"id": "P1", "MECHANISM": "first", "STRUCTURE": "dynamic programming"},
            {"id": "P9", "MECHANISM": "out of range"},       # ignored
        ]}

    monkeypatch.setattr(fp, "_llm_json", fake)
    idx = fp.FingerprintIndex(tmp_path / "fp.json")
    works = [dict(ECOLOGY), dict(CHATBOT), {"title": "no abstract", "url": "u3", "abstract": ""}]
    out = await fp.fingerprint_works(works, index=idx)
    assert out[fp._key(ECOLOGY)]["MECHANISM"] == "first"
    assert out[fp._key(CHATBOT)]["STRUCTURE"] == "graph traversal"     # normalised
    assert len(out) == 2 and len(calls) == 1
    again = await fp.fingerprint_works(works, index=fp.FingerprintIndex(tmp_path / "fp.json"))
    assert again == out and len(calls) == 1                              # served from disk


@pytest.mark.asyncio
async def test_problem_fingerprint_none_without_llm(monkeypatch):
    import case_engine.fingerprint as fp

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(fp, "_llm_json", nothing)
    assert await fp.fingerprint_problem("make my chatbot less boring") is None
