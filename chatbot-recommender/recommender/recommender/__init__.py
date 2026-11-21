"""Diversity-first chatbot recommender.

The three mechanisms from the user's spec, each backed by a real
open-source engine (install-and-customize, not reinvent):

1. similar-but-different -- Qdrant vector retrieval restricted to
   neighbour categories (networkx graph hop) + MMR diversification;
2. wildcards -- long-tail / unknown-author / orthogonal-topic /
   pure-random pools, chosen by a contextual bandit (Mab2Rec LinTS by
   default, Vowpal Wabbit or a Gorse server as alternatives);
3. anti-repetition -- hard seen-set + a port of the Hari semantic
   refractory idea (cosine suppression with decay).

Quick start::

    from recommender import Chatbot
    bot = Chatbot()
    reply = bot.chat("I've been watching videos about oil painting")
    print(reply.text)
    bot.feedback(reply.recommendations[0].item.id, "liked")
"""

from .catalog import Author, Catalog, Item
from .chatbot import Chatbot, Reply
from .config import RecommenderConfig
from .demo_data import build_demo_catalog
from .embeddings import TfidfEmbedder
from .engines import engine_report
from .mixer import Feed, FeedMixer, SessionState
from .mechanisms import (
    AntiRepetition,
    Recommendation,
    SimilarButDifferent,
    Wildcards,
)

__version__ = "0.1.0"

__all__ = [
    "AntiRepetition",
    "Author",
    "Catalog",
    "Chatbot",
    "Feed",
    "FeedMixer",
    "Item",
    "Recommendation",
    "RecommenderConfig",
    "Reply",
    "SessionState",
    "SimilarButDifferent",
    "TfidfEmbedder",
    "Wildcards",
    "build_demo_catalog",
    "engine_report",
]
