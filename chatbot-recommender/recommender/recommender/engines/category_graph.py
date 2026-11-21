"""Category-graph engines: the "see paintings -> recommend sculpture" hops.

``NetworkxCategoryGraph`` is the real default (networkx BFS over the
weighted edge list). ``Neo4jCategoryGraph`` is the env-gated adapter for
a live Neo4j GDS deployment (the user's named engine) -- the same
mechanism, Cypher-powered.
"""

from __future__ import annotations

import os
from typing import Protocol

from ..demo_data import CATEGORY_EDGES


class CategoryGraph(Protocol):
    def neighbors(self, category: str, max_hops: int = 2) -> dict[str, int]: ...
    def distance(self, source: str, target: str) -> int: ...
    def name(self) -> str: ...


class NetworkxCategoryGraph:
    def __init__(self, edges: list[tuple[str, str, float]] | None = None) -> None:
        import networkx as nx

        self._graph = nx.Graph()
        for source, target, _weight in edges or CATEGORY_EDGES:
            self._graph.add_edge(source, target)

    def name(self) -> str:
        return "networkx (embedded)"

    def neighbors(self, category: str, max_hops: int = 2) -> dict[str, int]:
        import networkx as nx

        if category not in self._graph:
            return {}
        lengths = nx.single_source_shortest_path_length(self._graph, category)
        return {
            neighbor: hop
            for neighbor, hop in lengths.items()
            if neighbor != category and hop <= max_hops
        }

    def distance(self, source: str, target: str) -> int:
        import networkx as nx

        try:
            return int(nx.shortest_path_length(self._graph, source, target))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return 10  # effectively disconnected


class Neo4jCategoryGraph:
    """Env-gated adapter (NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD)."""

    def __init__(self) -> None:
        uri = os.environ.get("NEO4J_URI")
        if not uri:
            raise RuntimeError(
                "Neo4jCategoryGraph requires NEO4J_URI (plus NEO4J_USER / "
                "NEO4J_PASSWORD). Offline runs use graph_engine='networkx'."
            )
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(
            uri,
            auth=(os.environ.get("NEO4J_USER", "neo4j"),
                  os.environ.get("NEO4J_PASSWORD", "")),
        )
        # expects (:Category {name})-[:NEIGHBOR*1..2]-(:Category)
        with self._driver.session() as session:
            session.run("MATCH (c:Category) RETURN count(c) AS n").single()

    def name(self) -> str:
        return "neo4j (remote)"

    def neighbors(self, category: str, max_hops: int = 2) -> dict[str, int]:
        query = (
            "MATCH (c:Category {name: $category})-[:NEIGHBOR*1..%d]-(n:Category) "
            "WHERE n.name <> $category RETURN DISTINCT n.name AS name, "
            "min(length(p)) AS hops" % max_hops
        )
        # Cypher needs the path named for length(); keep it simple and correct:
        query = (
            f"MATCH p=(c:Category {{name: $category}})-[:NEIGHBOR*1..{max_hops}]-(n:Category) "
            "WHERE n.name <> $category "
            "RETURN n.name AS name, min(length(p)) AS hops"
        )
        with self._driver.session() as session:
            rows = session.run(query, category=category).data()
        return {row["name"]: int(row["hops"]) for row in rows}

    def distance(self, source: str, target: str) -> int:
        query = (
            "MATCH p=shortestPath((a:Category {name: $source})-[:NEIGHBOR*..6]-"
            "(b:Category {name: $target})) RETURN length(p) AS hops"
        )
        with self._driver.session() as session:
            row = session.run(query, source=source, target=target).single()
        return int(row["hops"]) if row else 10
