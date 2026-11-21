"""Engine registry: named engines resolved once, with honest fallbacks.

The strategy the user set: install existing engines and customize, never
reinvent. This registry makes that explicit -- each slot (vector /
bandit / graph) is chosen by name; env-gated remote engines (Gorse,
Neo4j) fail loudly with instructions instead of silently degrading.
"""

from __future__ import annotations

from ..config import RecommenderConfig
from .bandit import (
    Bandit,
    EpsilonGreedyBandit,
    GorseExploreClient,
    Mab2RecBandit,
    VowpalWabbitBandit,
)
from .category_graph import CategoryGraph, NetworkxCategoryGraph, Neo4jCategoryGraph
from .vector_store import NumpyVectorStore, QdrantVectorStore, VectorStore


class EngineReport(dict):
    """Which engine serves which slot, and what happened if it could not."""

    @property
    def all_live(self) -> bool:
        return all(value.get("live", False) for value in self.values())


def resolve_vector_engine(name: str) -> VectorStore:
    if name == "qdrant":
        try:
            return QdrantVectorStore()
        except Exception as error:
            import logging
            logging.getLogger(__name__).info(
                "qdrant engine unavailable (%s); falling back to numpy vector store", error
            )
            return NumpyVectorStore()
    if name == "numpy":
        return NumpyVectorStore()
    raise ValueError(f"unknown vector engine: {name}")


def resolve_bandit_engine(name: str, arms: tuple[str, ...], seed: int) -> Bandit:
    if name == "mab2rec":
        try:
            return Mab2RecBandit(arms, seed=seed)
        except Exception as error:
            import logging
            logging.getLogger(__name__).info(
                "mab2rec engine unavailable (%s); falling back to epsilon-greedy bandit", error
            )
            return EpsilonGreedyBandit(arms, seed=seed)
    if name == "vowpal_wabbit":
        return VowpalWabbitBandit(arms, seed=seed)
    if name == "epsilon":
        return EpsilonGreedyBandit(arms, seed=seed)
    if name == "gorse":
        return GorseExploreClient(arms, seed=seed)
    raise ValueError(f"unknown bandit engine: {name}")


def resolve_graph_engine(name: str) -> CategoryGraph:
    if name == "networkx":
        return NetworkxCategoryGraph()
    if name == "neo4j":
        return Neo4jCategoryGraph()
    raise ValueError(f"unknown graph engine: {name}")


def engine_report(config: RecommenderConfig) -> EngineReport:
    report = EngineReport()
    for slot, name in (
        ("vector", config.vector_engine),
        ("bandit", config.bandit_engine),
        ("graph", config.graph_engine),
    ):
        entry: dict = {"engine": name}
        try:
            if slot == "vector":
                engine = resolve_vector_engine(name)
            elif slot == "bandit":
                engine = resolve_bandit_engine(name, config.wildcard_arms, config.seed)
            else:
                engine = resolve_graph_engine(name)
            entry["live"] = True
            entry["implementation"] = engine.name()
        except RuntimeError as error:
            entry["live"] = False
            entry["reason"] = str(error)
        report[slot] = entry
    return report
