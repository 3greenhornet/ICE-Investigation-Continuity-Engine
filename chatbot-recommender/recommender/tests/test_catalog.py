"""Catalog pool predicates -- the wildcard pools must be exactly what
their names claim."""

from __future__ import annotations

import pytest

from recommender.catalog import Catalog
from recommender.config import RecommenderConfig
from recommender.demo_data import build_demo_catalog

CONFIG = RecommenderConfig()


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return build_demo_catalog()


def test_catalog_is_deterministic() -> None:
    first = build_demo_catalog()
    second = build_demo_catalog()
    assert [item.id for item in first.items] == [item.id for item in second.items]
    assert [item.play_count for item in first.items] == [
        item.play_count for item in second.items
    ]


def test_catalog_contains_the_user_examples(catalog: Catalog) -> None:
    # old videos with ~100 plays
    long_tail = catalog.pool_long_tail_old(
        CONFIG.long_tail_max_plays, CONFIG.long_tail_min_age_days
    )
    assert len(long_tail) >= 20
    assert all(item.play_count <= CONFIG.long_tail_max_plays for item in long_tail)
    assert all(
        item.published_days_ago >= CONFIG.long_tail_min_age_days for item in long_tail
    )
    hundred_play_class = [item for item in long_tail if item.play_count < 250]
    assert len(hundred_play_class) >= 10  # the "100 plays" class is well populated

    # unknown authors
    unknowns = catalog.pool_unknown_authors(CONFIG.unknown_author_max_followers)
    assert len(unknowns) >= 10
    assert all(
        item.author.followers <= CONFIG.unknown_author_max_followers
        for item in unknowns
    )


def test_orthogonal_pool_excludes_focus_cluster(catalog: Catalog) -> None:
    distances = {
        "painting": 0,
        "sculpture": 1,
        "photography": 1,
        "architecture": 1,
        "philately": 5,
        "metro_maps": 5,
        "cooking": 5,
    }
    pool = catalog.pool_orthogonal("painting", distances)
    categories = {item.category for item in pool}
    assert categories == {"philately", "metro_maps", "cooking"}
    assert "painting" not in categories and "sculpture" not in categories


def test_orthogonal_pool_without_graph_info(catalog: Catalog) -> None:
    pool = catalog.pool_orthogonal("painting", {})
    categories = {item.category for item in pool}
    assert "painting" not in categories
    assert len(categories) >= 5  # everything else is fair game


def test_every_category_has_items(catalog: Catalog) -> None:
    counts = catalog.category_counts()
    assert all(count >= 3 for count in counts.values())
    assert len(counts) == 13
