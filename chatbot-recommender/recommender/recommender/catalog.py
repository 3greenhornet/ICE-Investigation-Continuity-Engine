"""Content catalog: items, authors, and the long-tail pools.

The catalog is deliberately engine-free (plain dataclasses) so every
mechanism and engine works against the same substrate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class Author:
    id: str
    name: str
    followers: int

    @property
    def is_unknown(self) -> bool:
        return self.followers < 150


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    kind: str                 # "video" | "article" | "artwork"
    category: str
    author: Author
    topics: tuple[str, ...]
    description: str
    published_days_ago: int
    play_count: int

    @property
    def is_long_tail_old(self, max_plays: int = 800, min_age_days: int = 365) -> bool:
        return self.play_count <= max_plays and self.published_days_ago >= min_age_days

    @property
    def text(self) -> str:
        """Everything the embedder is allowed to see."""
        return " ".join(
            (self.title, self.category, " ".join(self.topics), self.description)
        )


class Catalog:
    def __init__(self, items: Sequence[Item]):
        self.items: list[Item] = list(items)
        self.by_id: dict[str, Item] = {item.id: item for item in self.items}
        if len(self.by_id) != len(self.items):
            raise ValueError("duplicate item ids in catalog")
        self.authors: dict[str, Author] = {
            item.author.id: item.author for item in self.items
        }
        self.categories: list[str] = sorted({item.category for item in self.items})

    # ── wildcard pools (mechanism 2 candidate sources) ───────────────────
    def pool_long_tail_old(self, max_plays: int, min_age_days: int) -> list[Item]:
        return [
            item
            for item in self.items
            if item.play_count <= max_plays
            and item.published_days_ago >= min_age_days
        ]

    def pool_unknown_authors(self, max_followers: int) -> list[Item]:
        return [item for item in self.items if item.author.followers <= max_followers]

    def pool_orthogonal(
        self, focus_category: str | None, category_distance: Mapping[str, int]
    ) -> list[Item]:
        """Items whose category is graph-far from the user's focus.

        ``category_distance`` maps category -> hop distance from the focus
        (provided by the graph engine). Categories at or beyond
        ``orthogonal_min_graph_distance`` count as unrelated topics.
        """
        far = {
            category
            for category, distance in category_distance.items()
            if distance >= 3
        }
        if not far:
            # No graph information (or focus unknown): orthogonal = everything
            # outside the focus category itself.
            far = {category for category in self.categories if category != focus_category}
        return [item for item in self.items if item.category in far]

    def pool_pure_random(self) -> list[Item]:
        return list(self.items)

    def items_in_categories(self, categories: Iterable[str]) -> list[Item]:
        wanted = set(categories)
        return [item for item in self.items if item.category in wanted]

    def category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.category] = counts.get(item.category, 0) + 1
        return counts
