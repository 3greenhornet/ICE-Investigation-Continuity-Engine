"""case_engine/nli.py — Natural Language Inference for claim-hypothesis relationships.

Uses RoBERTa-large-MNLI to classify whether a claim supports, contradicts,
or is neutral relative to a hypothesis.

The model is loaded lazily (first call), cached in-process, and returns None
on any load or inference failure (never raises). The pipeline falls back to
manual classification when the model is unavailable.

New module (ICE fixes doc Part 2 Section 10, fix 2I — Part 1's B.13 gap:
no automatic classification of claim-hypothesis relationships; every
add_edge call required a human decision).

Optional dependency: pip install -e ".[nli]" (transformers + torch).
The model (~1.4 GB) is downloaded on first use and cached in
~/.cache/huggingface/.
"""

import asyncio
import logging
from typing import Literal, Optional

logger = logging.getLogger(__name__)

_nli_model = None
_nli_tokenizer = None
_NLI_MODEL_NAME = "roberta-large-mnli"
_NLI_LOAD_FAILED = False


def _load_nli() -> bool:
    """Load the NLI model. Returns True on success, False on failure.

    On failure, sets _NLI_LOAD_FAILED = True so subsequent calls do not
    retry (avoiding 1.4 GB download attempts on every call in a broken
    environment).
    """
    global _nli_model, _nli_tokenizer, _NLI_LOAD_FAILED
    if _nli_model is not None:
        return True
    if _NLI_LOAD_FAILED:
        return False
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        logger.info("Loading NLI model %s (first call, may take a minute)", _NLI_MODEL_NAME)
        _nli_tokenizer = AutoTokenizer.from_pretrained(_NLI_MODEL_NAME)
        _nli_model = AutoModelForSequenceClassification.from_pretrained(_NLI_MODEL_NAME)
        _nli_model.eval()
        logger.info("NLI model %s loaded", _NLI_MODEL_NAME)
        return True
    except Exception as exc:
        logger.warning(
            "NLI model load failed (%s): %s — falling back to manual classification. "
            "Install with: pip install -e '.[nli]'",
            _NLI_MODEL_NAME, exc,
        )
        _NLI_LOAD_FAILED = True
        return False


async def classify_claim_against_hypothesis(
    claim_statement: str,
    hypothesis_statement: str,
    support_threshold: float = 0.7,
    contradict_threshold: float = 0.7,
) -> Optional[Literal["SUPPORTS", "CONTRADICTS", "NEUTRAL"]]:
    """Classify the relationship between a claim and a hypothesis.

    Returns:
        "SUPPORTS" if entailment probability > support_threshold
        "CONTRADICTS" if contradiction probability > contradict_threshold
        "NEUTRAL" otherwise
        None if the model is unavailable

    Never raises. Runs inference in a thread pool to avoid blocking the
    event loop.

    HONEST NOTE (doc Part 2 §12 shortcut #13): the NLI model will
    sometimes misclassify. It only sees surface text, so a claim whose
    connection to the hypothesis is inferential (e.g. "latency rose from
    200ms to 800ms" vs. "the connection pool is exhausted") may come back
    NEUTRAL. The proposed-edges middle ground in claim.extract_claims
    (user confirmation before any real edge) mitigates false positives
    without eliminating false negatives.
    """
    if not _load_nli():
        return None

    def _infer() -> str:
        import torch
        inputs = _nli_tokenizer(
            hypothesis_statement,  # premise
            claim_statement,       # hypothesis
            return_tensors="pt",
            truncation=True,
            max_length=512,
        )
        with torch.no_grad():
            logits = _nli_model(**inputs).logits
        probs = torch.softmax(logits, dim=-1)[0].tolist()
        # RoBERTa-large-MNLI label order: [contradiction, neutral, entailment]
        contradiction, neutral, entailment = probs
        if entailment > support_threshold:
            return "SUPPORTS"
        if contradiction > contradict_threshold:
            return "CONTRADICTS"
        return "NEUTRAL"

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _infer)
    except Exception as exc:
        logger.warning("NLI inference failed: %s", exc)
        return None