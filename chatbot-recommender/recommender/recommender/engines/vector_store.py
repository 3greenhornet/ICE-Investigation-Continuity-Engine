"""Vector-store engines.

``QdrantVectorStore`` is the real engine (the qdrant-client from the
user's named "LangChain/Qdrant MMR" stack, running embedded/in-memory
here, server-mode on deployment). ``NumpyVectorStore`` is the
dependency-free fallback so the mechanism never breaks.
"""

from __future__ import annotations

from typing import Protocol, Sequence

import numpy as np

from ..catalog import Item


class VectorStore(Protocol):
    def build(self, items: Sequence[Item], vectors: np.ndarray) -> None: ...
    def search(
        self,
        query_vector: np.ndarray,
        limit: int,
        category_whitelist: Sequence[str] | None = None,
        exclude_ids: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Return (item_id, cosine similarity) best-first."""
    def vector_of(self, item_id: str) -> np.ndarray | None: ...
    def name(self) -> str: ...


class QdrantVectorStore:
    """qdrant-client in embedded (in-memory) mode; payload = category."""

    def __init__(self) -> None:
        from qdrant_client import QdrantClient, models

        self._models = models
        self._client = QdrantClient(":memory:")
        self._collection = "catalog"
        self._vectors: dict[str, np.ndarray] = {}
        self._built = False

    def name(self) -> str:
        return "qdrant (embedded)"

    def build(self, items: Sequence[Item], vectors: np.ndarray) -> None:
        models = self._models
        dim = int(vectors.shape[1])
        if self._client.collection_exists(self._collection):
            self._client.delete_collection(self._collection)
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=models.VectorParams(
                size=dim, distance=models.Distance.COSINE
            ),
        )
        points = [
            models.PointStruct(
                id=index,
                vector=[float(value) for value in vectors[index]],
                payload={"item_id": item.id, "category": item.category},
            )
            for index, item in enumerate(items)
        ]
        self._client.upload_points(collection_name=self._collection, points=points)
        for index, item in enumerate(items):
            self._vectors[item.id] = vectors[index]
        self._built = True

    def search(
        self,
        query_vector: np.ndarray,
        limit: int,
        category_whitelist: Sequence[str] | None = None,
        exclude_ids: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]:
        models = self._models
        must: list = []
        if category_whitelist:
            must.append(
                models.FieldCondition(
                    key="category", match=models.MatchAny(any=list(category_whitelist))
                )
            )
        query_filter = models.Filter(must=must) if must else None
        result = self._client.query_points(
            collection_name=self._collection,
            query=[float(value) for value in query_vector],
            limit=max(limit * 3, limit),  # headroom for post-filters
            query_filter=query_filter,
        ).points
        excluded = set(exclude_ids or [])
        hits: list[tuple[str, float]] = []
        for point in result:
            item_id = point.payload["item_id"]
            if item_id in excluded:
                continue
            hits.append((item_id, float(point.score)))
            if len(hits) >= limit:
                break
        return hits

    def vector_of(self, item_id: str) -> np.ndarray | None:
        return self._vectors.get(item_id)


class NumpyVectorStore:
    """Pure-numpy fallback: cosine scores over a dense matrix."""

    def __init__(self) -> None:
        self._matrix: np.ndarray | None = None
        self._ids: list[str] = []
        self._categories: list[str] = []
        self._vectors: dict[str, np.ndarray] = {}

    def name(self) -> str:
        return "numpy (fallback)"

    def build(self, items: Sequence[Item], vectors: np.ndarray) -> None:
        self._matrix = vectors.astype(np.float32)
        self._ids = [item.id for item in items]
        self._categories = [item.category for item in items]
        self._vectors = {item.id: vectors[index] for index, item in enumerate(items)}

    def search(
        self,
        query_vector: np.ndarray,
        limit: int,
        category_whitelist: Sequence[str] | None = None,
        exclude_ids: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]:
        if self._matrix is None:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        query = query / (np.linalg.norm(query) + 1e-9)
        scores = self._matrix @ query
        whitelist = set(category_whitelist or [])
        excluded = set(exclude_ids or [])
        ranked = sorted(
            (
                (self._ids[index], float(scores[index]))
                for index in range(len(self._ids))
                if self._ids[index] not in excluded
                and (not whitelist or self._categories[index] in whitelist)
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return ranked[:limit]

    def vector_of(self, item_id: str) -> np.ndarray | None:
        return self._vectors.get(item_id)
