"""serendipity/taxonomy.py — Instagram-style concept hierarchy (Mechanism A).

If a user likes Oil Painting (child), the system looks up to Fine Arts
(parent) and steps sideways to Sculpting or Pottery (siblings), one more
level out to cousins. Also implements the multimodal intersection: items
bridging two of the user's interests ("Raspberry-Pi sous-vide cooking"
sits at the intersection of coding and cooking) get an adjacency boost —
this is the mechanism the original code base designed as
GenerativityEstimate.bridge_score and never implemented (stubbed at 0.5).
"""
from collections import Counter
from typing import Dict, Iterable, List, Optional, Set, Tuple


class Taxonomy:
    """Built from {category: parent} pairs. Root = "" (a category whose
    parent is "" sits directly under the root)."""

    def __init__(self, parents: Dict[str, str]):
        self.parents = dict(parents)
        self._children: Dict[str, List[str]] = {}
        for child, parent in parents.items():
            self._children.setdefault(parent, []).append(child)
        for k, v in self._children.items():
            v.sort()

    def parent(self, category: str, up: int = 1) -> str:
        cur = category
        for _ in range(up):
            cur = self.parents.get(cur, "")
            if cur == "":
                break
        return cur

    def siblings(self, category: str) -> List[str]:
        p = self.parents.get(category)
        if p is None:
            return []
        return [c for c in self._children.get(p, []) if c != category]

    def cousins(self, category: str) -> List[str]:
        p = self.parents.get(category)
        if p is None:
            return []
        gp = self.parents.get(p)
        if gp is None:
            return []
        same_generation = self._children.get(gp, [])
        out: List[str] = []
        for c in same_generation:
            if c == p:
                continue
            out.extend(self._children.get(c, []))
        return out

    def adjacent_categories(self, category: str) -> List[str]:
        """Siblings first (similar-yet-different), cousins second (further
        out but still in the family). Deduped, deterministic order."""
        seen: Set[str] = set()
        out: List[str] = []
        for cat in self.siblings(category) + self.cousins(category):
            if cat not in seen:
                seen.add(cat)
                out.append(cat)
        return out

    def lca_depth(self, a: str, b: str) -> Optional[int]:
        """Depth of the lowest common ancestor; None if unrelated.
        The synthetic root marker "" never counts as kinship. Used by the
        similarity fallback when no embeddings exist."""
        chain_a = self._chain(a)
        chain_b = self._chain(b)
        for i, ca in enumerate(chain_a):
            if ca in chain_b:
                j = chain_b.index(ca)
                return i + j
        return None

    def _chain(self, cat: str) -> List[str]:
        """Real ancestors only (the "" root marker is excluded)."""
        out = [cat]
        cur = cat
        while True:
            nxt = self.parents.get(cur)
            if not nxt or nxt == "":
                break
            out.append(nxt)
            cur = nxt
        return out


def interest_profile(
    engaged_tags: Iterable[Iterable[str]],
    top: int = 3,
) -> List[str]:
    """The user's interest categories from what they ENGAGED with
    (not what was shown — showing measures supply, engaging measures taste)."""
    counts = Counter()
    for tags in engaged_tags:
        for t in tags:
            counts[t] += 1
    return [tag for tag, _ in counts.most_common(top)]


def multimodal_bridges(
    items: List[Tuple[str, str, Tuple[str, ...]]],   # (id, title, tags)
    interests: List[str],
) -> List[str]:
    """Items tagged with >= 2 of the user's interest categories — the
    Instagram multimodal intersection. Returns ids."""
    if len(interests) < 2:
        return []
    inter = set(interests)
    out = []
    for item_id, _title, tags in items:
        hits = inter.intersection(tags)
        if len(hits) >= 2:
            out.append(item_id)
    return out
