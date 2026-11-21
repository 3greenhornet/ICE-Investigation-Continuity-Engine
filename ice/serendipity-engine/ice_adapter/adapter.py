"""ice_adapter/adapter.py — binds the serendipity engine to a live
Hari/ICE CuriosityGraph WITHOUT modifying a single ice/ file.

What it reads (never writes, except through the graph's own designed
fields, listed under 'revives'):
    * node ids, core_question, importance, exploration_progress
    * edge weights (the Hebbian co-activation graph ICE already persists
      in Postgres)

What it revives (fields ICE defined, persisted, READ — but never updated
by anything, i.e. dead dials):
    * exploration_progress — ICE's attention code multiplies exploratory
      potential by (1 - exploration_progress), yet no code ever advanced
      it. This adapter advances it on feedback, so the cognitive layer's
      own attention math starts learning "enough about this".
    * edge weights — reinforced on co-engagement (the graph's Hebbian
      update_edge path exists; nothing called it from the user side).

Usage:
    from ice_adapter.adapter import IceSerendipityAdapter
    adapter = IceSerendipityAdapter(session_id, graph_manager)
    engine = adapter.build_engine(store_path="~/.serendipity/sam.sqlite")
    ...
    adapter.sync()                    # re-read graph after it evolves
    picks = engine.suggest(session_id, n=2, context_tags=["painting"])
    # ... user reacts ...
    engine.record(picks[0].token, "engaged")
"""
import re
from typing import Dict, List, Optional, Tuple

import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)   # make `serendipity` importable when used in-tree

from serendipity import SerendipityConfig, SerendipityEngine, Item  # noqa: E402


# Coarse domain taxonomy for free-text curiosity questions. Extend or
# replace with set_taxonomy(); this is a bootstrap, not a ceiling.
_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "art": ["paint", "sculpt", "sketch", "art ", "artist", "drawing", "pottery",
            "ceramic", "architecture", "design language"],
    "science": ["physics", "quantum", "star", "black hole", "chemis", "biolog",
                "entrop", "relativ", "particle", "atom", "evolution", "neuro"],
    "tech": ["code", "coding", "algorithm", "software", "program", "comput",
             "network", "database", "machine learning", "ai ", "robot"],
    "history": ["histor", "ancient", "empire", "war ", "civilization", "century",
                "medieval", "archae"],
    "cooking": ["cook", "bak", "sourdough", "food", "recipe", "kitchen", "ferment",
                "gastronom", "coffee", "tea "],
    "music": ["music", "song", "compos", "guitar", "piano", "jazz", "opera"],
    "sports": ["sport", "football", "soccer", "basketball", "cricket", "olympic"],
    "philosophy": ["philosoph", "ethic", "moral", "conscious", "exist", "meaning of",
                   "free will", "epistem"],
}

_DOMAIN_TREE = {
    "art": "culture", "music": "culture", "history": "culture",
    "science": "nature", "tech": "nature",
    "cooking": "craft", "philosophy": "culture", "sports": "play",
    "culture": "", "nature": "", "craft": "", "play": "",
}


def guess_category(question: str) -> str:
    q = f" {question.lower()} "
    for domain, keys in _DOMAIN_KEYWORDS.items():
        if any(k in q for k in keys):
            return domain
    return ""


def question_tags(question: str) -> Tuple[str, ...]:
    """Content words > 3 chars, lowercased, deduped — the tag fallback
    when a host has no embeddings."""
    words = re.findall(r"[a-z]{4,}", question.lower())
    return tuple(sorted(set(words))[:8])


class IceSerendipityAdapter:
    def __init__(self, session_id: str, graph_manager, config: Optional[SerendipityConfig] = None):
        self.session_id = session_id
        self.graph = graph_manager
        self.config = config or SerendipityConfig()
        self._engine: Optional[SerendipityEngine] = None
        self._recent_engaged_ids: List[str] = []

    # ------------------------------------------------------------------
    # graph -> engine inputs
    # ------------------------------------------------------------------
    def snapshot_items(self) -> List[Item]:
        g = self._nx()
        items: List[Item] = []
        for nid, data in (g.nodes(data=True) if g else []):
            q = str(data.get("core_question", nid))
            importance = float(data.get("importance", 0.5) or 0.5)
            items.append(Item(
                id=nid,
                title=q,
                tags=question_tags(q),
                category=guess_category(q),
                quality=max(0.2, min(1.0, importance)),
                embedding=None,
                n_shown_global=int(float(data.get("exploration_progress", 0.0)) * 10),
            ))
        return items

    def snapshot_adjacency(self) -> Dict[str, List[Tuple[str, float]]]:
        g = self._nx()
        adj: Dict[str, List[Tuple[str, float]]] = {}
        for u, v, data in (g.edges(data=True) if g else []):
            w = float(data.get("weight", 0.1) or 0.1)
            adj.setdefault(u, []).append((v, w))
            adj.setdefault(v, []).append((u, w))   # CuriosityGraph is undirected
        return adj

    def _nx(self):
        g = getattr(self.graph, "_graph", None)
        return g

    # ------------------------------------------------------------------
    # feedback -> graph (the α/β loop reaches the cognitive substrate)
    # ------------------------------------------------------------------
    def _on_feedback(self, item: Item, slot: str, signal: str) -> None:
        g = self._nx()
        if g is None or item.id not in g:
            return
        node = g.nodes[item.id]
        prog = min(1.0, float(node.get("exploration_progress", 0.0) or 0.0))

        if signal == "engaged":
            # advance the dead dial: the attention layer's own
            # (1 - exploration_progress) damping starts differentiating
            node["exploration_progress"] = min(1.0, prog + 0.25)
            # Hebbian: bond this item to the user's other recent interests
            for other in self._recent_engaged_ids[-3:]:
                if other == item.id or other not in g:
                    continue
                if g.has_edge(item.id, other):
                    w = float(g[item.id][other].get("weight", 0.0))
                    g[item.id][other]["weight"] = min(1.0, w + 0.05)
                else:
                    g.add_edge(item.id, other, weight=0.05)
        elif signal == "ignored":
            node["exploration_progress"] = min(1.0, prog + 0.1)
        else:  # negative
            node["exploration_progress"] = min(1.0, prog + 0.25)
            node["importance"] = max(0.05, float(node.get("importance", 0.5)) * 0.7)

        if signal == "engaged":
            self._recent_engaged_ids.append(item.id)
            del self._recent_engaged_ids[:-8]

    # ------------------------------------------------------------------
    # build / sync
    # ------------------------------------------------------------------
    def build_engine(self, store_path: Optional[str] = None, seed: Optional[int] = None) -> SerendipityEngine:
        cfg = self.config
        if store_path is not None:
            cfg.store_path = store_path
        if seed is not None:
            cfg.seed = seed
        self._recent_engaged_ids = []
        self._engine = SerendipityEngine(
            config=cfg,
            items=self.snapshot_items(),
            adjacency=self.snapshot_adjacency(),
            taxonomy=dict(_DOMAIN_TREE),
            on_feedback=self._on_feedback,
        )
        return self._engine

    def sync(self) -> None:
        """Re-read the graph after it evolves (call every few turns)."""
        if self._engine is None:
            raise RuntimeError("build_engine() first")
        current = {it.id: it for it in self.snapshot_items()}
        with self._engine._lock:
            self._engine._items = current
        self._engine.set_adjacency(self.snapshot_adjacency())
        self._engine.set_taxonomy(dict(_DOMAIN_TREE))

    @staticmethod
    def goals_to_items(goals: List[dict]) -> List[Item]:
        """Bridge Evolving Project Model goals into Serendipity items."""
        items: List[Item] = []
        for g in goals:
            gid = g.get("goal_id", "")
            stmt = g.get("statement", "")
            kind = g.get("kind", "goal")
            clarity = int(g.get("clarity_level", 1) or 1)
            quality = min(1.0, max(0.2, clarity / 4.0))
            items.append(Item(
                id=f"goal:{gid}",
                title=f"[{kind.upper()}] {stmt}",
                tags=question_tags(stmt) + (kind,),
                category=guess_category(stmt),
                quality=quality,
            ))
        return items

    @staticmethod
    def research_questions_to_items(questions: List[dict]) -> List[Item]:
        """Bridge STORM-lite research questions into Serendipity items."""
        items: List[Item] = []
        for q in questions:
            qid = q.get("gap_id") or q.get("question_id") or "q"
            stmt = q.get("question") or q.get("description", "")
            persp = q.get("perspective", "research")
            items.append(Item(
                id=f"research:{qid}",
                title=stmt,
                tags=question_tags(stmt) + (str(persp),),
                category=guess_category(stmt),
                quality=0.8,
            ))
        return items
