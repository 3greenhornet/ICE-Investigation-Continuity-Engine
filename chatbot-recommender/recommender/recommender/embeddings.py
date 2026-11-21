"""Embeddings: TF-IDF over title + category + topics + description.

A real, standard text-vectorization approach (scikit-learn) that runs
fully offline and deterministically. The ``Embedder`` protocol is the
seam for swapping in a neural encoder (sentence-transformers, an
embedding API, ...) later without touching the mechanisms: everything
downstream only requires unit-length vectors and a ``dim``.
"""

from __future__ import annotations

from typing import Protocol, Sequence

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize


class Embedder(Protocol):
    dim: int

    def fit(self, texts: Sequence[str]) -> "Embedder": ...
    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class TfidfEmbedder:
    """L2-normalized TF-IDF vectors; deterministic; sublinear TF."""

    def __init__(self, max_features: int = 4096):
        self._vectorizer = TfidfVectorizer(
            lowercase=True,
            sublinear_tf=True,
            max_features=max_features,
            token_pattern=r"(?u)\b\w\w+\b",
        )
        self.dim = 0

    def fit(self, texts: Sequence[str]) -> "TfidfEmbedder":
        self._vectorizer.fit(list(texts))
        self.dim = len(self._vectorizer.vocabulary_)
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        matrix = self._vectorizer.transform(list(texts)).toarray().astype(np.float32)
        return normalize(matrix, norm="l2", axis=1)
