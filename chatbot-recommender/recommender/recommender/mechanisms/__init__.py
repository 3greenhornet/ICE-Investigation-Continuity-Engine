"""Mechanisms package: the three contracts from the user's spec."""

from .anti_repetition import AntiRepetition
from .similar_but_different import Recommendation, SimilarButDifferent
from .wildcards import Wildcards

__all__ = [
    "AntiRepetition",
    "Recommendation",
    "SimilarButDifferent",
    "Wildcards",
]
